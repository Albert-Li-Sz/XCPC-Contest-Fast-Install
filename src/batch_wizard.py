"""Interactive control-side wizard; Ansible arguments and credentials stay internal."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import textwrap

from common import InstallError, host
import ui

ROOT = Path(__file__).resolve().parent


def ssh_user(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*[$]?", value):
        raise InstallError("SSH 用户名格式无效。")
    return value


def collect():
    print("\n批量部署向导：这台机器仅作为控制端，下面输入远程评测机信息。", flush=True)
    api = ui.field("DOMjudge 主站地址（可直接填 IP）", validate=ui.api_address)
    api_user = ui.field("DOMjudge API 账号", "judgehost", ui.username)
    api_password = ui.field("评测机 API 密码（隐藏输入，不是 SSH/admin 密码）", validate=ui.token, secret=True)
    count = ui.field("部署多少台评测机", "1", lambda v: ui.integer(v, 1, 1000))
    user = ui.field("这些机器的 SSH 登录用户名", "root", ssh_user)
    port = ui.field("SSH 端口", "22", lambda v: ui.integer(v, 1, 65535))
    auth = ui.choose("SSH 登录方式", [("1", "SSH 密钥 / ssh-agent"), ("2", "密码登录")], "1")
    common = dict(ansible_user=user, ansible_port=port, ansible_python_interpreter="/usr/bin/python3",
                  judge_api_url=api, judge_api_user=api_user, judge_timezone="Asia/Shanghai")
    private = dict(judge_api_password=api_password)
    separate_passwords = False
    if auth == "1":
        key = ui.field("SSH 私钥文件（回车使用默认密钥或 ssh-agent）", validate=ui.existing_file, optional=True)
        if key:
            common["ansible_ssh_private_key_file"] = key
    else:
        separate_passwords = ui.choose("各机器 SSH 密码", [("1", "所有机器使用相同密码"),
                                                        ("2", "每台单独填写")], "1") == "2"
        if not separate_passwords:
            private["ansible_password"] = ui.field("SSH 登录密码（隐藏输入）", secret=True)
    if user != "root":
        if ui.choose("sudo 提权方式", [("1", "无需密码"), ("2", "使用同一个 sudo 密码")], "1") == "2":
            private["ansible_become_password"] = ui.field("sudo 密码（隐藏输入）", secret=True)
    forks = ui.field("同时部署几台", str(min(3, count)), lambda v: ui.integer(v, 1, count))
    if ui.choose("其他设置", [("1", "使用默认设置"), ("2", "设置时区或私有 API 证书")], "1") == "2":
        common["judge_timezone"] = ui.field("时区", "Asia/Shanghai", ui.timezone)
        ca = ui.field("私有 CA 公开证书文件（回车使用系统信任）", validate=ui.existing_file, optional=True)
        if ca:
            common["judge_api_ca_file"] = ca
    hosts = {}
    endpoints = set()
    for number in range(1, count + 1):
        print(f"\n第 {number}/{count} 台评测机", flush=True)
        while True:
            address = ui.field("SSH IPv4 或域名", validate=host)
            if address not in endpoints:
                endpoints.add(address)
                break
            print("这个 SSH 地址已填写，不能重复部署同一台。", flush=True)
        while True:
            name = ui.field("DOMjudge 中的唯一评测机短名称", f"judge{number:02d}", ui.judge_name)
            if name not in hosts:
                break
            print("该评测机名称已使用，请重新填写。", flush=True)
        cpus = ui.field("该机评测 CPU 编号（例如 1 或 1,3）", "1", ui.cpus)
        item = dict(ansible_host=address, judge_hostname=name, judge_cpus=cpus)
        if separate_passwords:
            item["ansible_password"] = ui.field("这台机器的 SSH 密码（隐藏输入）", secret=True)
        hosts[name] = item
    inventory = dict(all=dict(children=dict(judgehosts=dict(vars=common, hosts=hosts))))
    return inventory, private, forks


def show_summary(inventory, forks):
    group = inventory["all"]["children"]["judgehosts"]
    print(f"\n主站 API：{group['vars']['judge_api_url']}\n同时部署：{forks} 台", flush=True)
    for name, item in group["hosts"].items():
        print(f"  {name}  SSH {item['ansible_host']}  CPU {item['judge_cpus']}", flush=True)
    print("所有密码均隐藏，不会保存在项目清单中。", flush=True)


def stage_project(destination):
    """Reconstruct exactly the entry being run, including from process substitution."""
    destination = Path(destination)
    (destination / "batch").mkdir()
    packed = ROOT / "payload.tar.gz"
    if packed.is_file():
        payload = packed.read_bytes()
        template = (ROOT / "bootstrap.txt").read_text()
        encoded = "\n".join(textwrap.wrap(base64.b64encode(payload).decode(), 100))
        main = template.replace("@@PAYLOAD@@", encoded).replace("@@PAYLOAD_SHA@@", hashlib.sha256(payload).hexdigest())
        playbook = ROOT / "batch/deploy.yml"
        (destination / "main.sh").write_text(main)
    else:
        project = ROOT.parent
        if ROOT.name != "src" or not (project / "scripts/build.py").is_file():
            raise InstallError("找不到完整安装入口；请重新运行公开的一行安装命令。")
        shutil.copyfile(project / "main.sh", destination / "main.sh")
        playbook = project / "batch/deploy.yml"
    (destination / "main.sh").chmod(0o700)
    shutil.copyfile(playbook, destination / "batch/deploy.yml")


def ansible_command(stage):
    executable = shutil.which("ansible-playbook")
    if executable:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, check=False)
        version = re.search(r"core (\d+)\.(\d+)\.", result.stdout)
        if result.returncode == 0 and version and version[1] == "2" and 19 <= int(version[2]) <= 21:
            return executable
    if sys.version_info < (3, 11):
        raise InstallError("批量控制端需要 Python 3.11 或以上。")
    print("准备临时 Ansible 环境，执行结束后自动清理。", flush=True)
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    if sys.platform.startswith("linux") and os.geteuid() == 0 and shutil.which("apt-get"):
        for command in [["apt-get", "update"], ["apt-get", "-o", "Dpkg::Lock::Timeout=300", "install", "-y", "python3-venv"]]:
            subprocess.run(command, env={**env, "DEBIAN_FRONTEND": "noninteractive"}, check=True)
    venv = stage / "ansible-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, env=env)
    subprocess.run([str(venv / "bin/python"), "-m", "pip", "install", "--no-cache-dir",
                    "ansible-core>=2.19,<2.22"], check=True, env=env)
    return str(venv / "bin/ansible-playbook")


def verify_host_keys(inventory, stage):
    """Use known keys; explicitly confirm new fingerprints before any password is sent."""
    group = inventory["all"]["children"]["judgehosts"]
    port = group["vars"]["ansible_port"]
    approved = []
    for name, item in group["hosts"].items():
        address = item["ansible_host"]
        lookup = address if port == 22 else f"[{address}]:{port}"
        known = Path.home() / ".ssh/known_hosts"
        found = subprocess.run(["ssh-keygen", "-F", lookup, "-f", str(known)],
                               capture_output=True, text=True, check=False)
        if found.returncode == 0:
            approved.extend(line for line in found.stdout.splitlines() if line and not line.startswith("#"))
            continue
        scan = subprocess.run(["ssh-keyscan", "-T", "10", "-p", str(port), "-t", "ed25519,rsa", address],
                              capture_output=True, text=True, timeout=45, check=False)
        keys = [line for line in scan.stdout.splitlines() if len(line.split()) == 3
                and line.split()[0] == lookup and line.split()[1] in {"ssh-ed25519", "ssh-rsa"}]
        if not keys:
            raise InstallError(f"无法读取 {name} 的 SSH 主机密钥，请检查地址、端口和网络。")
        candidate = stage / "candidate-host-keys"
        candidate.write_text("\n".join(keys) + "\n")
        fingerprint = subprocess.run(["ssh-keygen", "-l", "-f", str(candidate)], capture_output=True, text=True, check=False)
        if fingerprint.returncode:
            raise InstallError(f"{name} 的 SSH 公钥格式无效。")
        print(f"\n首次连接 {name} ({address})，请核对 SSH 主机公钥指纹：\n" + fingerprint.stdout, flush=True)
        if ui.choose("是否信任这些主机公钥", [("1", "指纹正确，继续"), ("0", "取消部署")]) != "1":
            raise InstallError("未确认 SSH 主机密钥，已取消部署。")
        approved.extend(keys)
    pinned = stage / "known_hosts"
    pinned.write_text("\n".join(approved) + "\n")
    pinned.chmod(0o600)
    return "-o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=" + shlex.quote(str(pinned))


def deploy(inventory, private, forks):
    with tempfile.TemporaryDirectory(prefix="xcpc-batch-") as directory:
        stage = Path(directory)
        stage_project(stage)
        for name, data in [("inventory.json", inventory), ("credentials.json", private)]:
            file = stage / name
            file.write_text(json.dumps(data, ensure_ascii=False))
            file.chmod(0o600)
        executable = ansible_command(stage)
        ssh_options = verify_host_keys(inventory, stage)
        # Use a known config, so a nearby ansible.cfg cannot turn on plaintext argument logging.
        config = stage / "ansible.cfg"
        config.write_text("[defaults]\nhost_key_checking=True\nretry_files_enabled=False\ndisplay_args_to_stdout=False\n")
        return subprocess.call([executable, "-i", str(stage / "inventory.json"),
                                str(stage / "batch/deploy.yml"), "-f", str(forks),
                                "--extra-vars", "@" + str(stage / "credentials.json"),
                                "--ssh-common-args", ssh_options],
                               env={**os.environ, "ANSIBLE_CONFIG": str(config),
                                    "ANSIBLE_HOST_KEY_CHECKING": "True", "ANSIBLE_DISPLAY_ARGS_TO_STDOUT": "False",
                                    "ANSIBLE_DEBUG": "False"},
                               cwd=stage)


def main():
    if not all(shutil.which(command) for command in ["ssh", "ssh-keygen", "ssh-keyscan"]):
        raise InstallError("控制端需要 OpenSSH 客户端（ssh、ssh-keygen、ssh-keyscan）。")
    while True:
        inventory, private, forks = collect()
        show_summary(inventory, forks)
        choice = ui.choose("下一步", [("1", "确认并开始批量部署"), ("2", "重新填写"), ("0", "退出，不部署")])
        if choice == "0":
            return 0
        if choice == "1":
            result = deploy(inventory, private, forks)
            if result == 0:
                print("批量部署完成。请核对各机检查结果，并进行真实判题验收。", flush=True)
            else:
                print("部分机器未完成，请查看上方逐机结果与目标机的私有安装日志。", flush=True)
            return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print("错误：" + str(error), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("已取消；远端可能已开始的安装请先检查状态。", file=sys.stderr)
        raise SystemExit(130)
