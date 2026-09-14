"""Build exact-version judgehost image and run one managed container per CPU."""
import grp
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import time

from common import (InstallError, CACHE, STATE_DIR, mkdir, write, link, request, wait_for)

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
IMAGE = "xcpc-local/judgehost:9.0.1"
BASE_IMAGE = "domjudge/judgehost@sha256:4c01f07e49023bcadd92255786372ec4c5fb5335bec5c9366f07b1fdddb28567"
CONFIG = Path("/etc/xcpc-judgehost")
OWNER = "xcpc-fast-install"


def inspect(kind, name):
    result = subprocess.run(DOCKER + [kind, "inspect", name], capture_output=True, text=True, check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def container_name(cpu):
    return f"xcpc-judgehost-{cpu}"


def uid_for(cpu):
    return 62860 + cpu


def spec(cfg, cpu):
    """Public run configuration; password content never goes in argv or env."""
    return {
        "cpu": cpu, "hostname": cfg["hostname"], "api_url": cfg["api_url"],
        "api_user": cfg["api_user"], "timezone": cfg["timezone"], "uid": uid_for(cpu),
        "image": IMAGE, "base_image": BASE_IMAGE,
    }


def spec_hash(cfg, cpu):
    return hashlib.sha256(json.dumps(spec(cfg, cpu), sort_keys=True).encode()).hexdigest()


def run_args(cfg, cpu):
    name = container_name(cpu)
    return DOCKER + ["run", "-d", "--name", name, "--hostname", cfg["hostname"],
        "--add-host", cfg["hostname"] + ":127.0.1.1",
        "--restart", "unless-stopped", "--privileged", "--cgroupns=host",
        "--network", "host", "--cpuset-cpus", str(cpu),
        "--label", "org.xcpc.owner=" + OWNER,
        "--label", "org.xcpc.spec=" + spec_hash(cfg, cpu),
        "--log-driver", "json-file", "--log-opt", "max-size=20m", "--log-opt", "max-file=5",
        "--mount", "type=bind,src=/sys/fs/cgroup,dst=/sys/fs/cgroup",
        "--mount", f"type=bind,src={CONFIG}/secrets,dst=/run/xcpc-secrets,readonly",
        "--mount", f"type=volume,src={name}-judgings,dst=/opt/domjudge/judgehost/judgings",
        "--mount", f"type=volume,src={name}-logs,dst=/opt/domjudge/judgehost/log",
        "--env", "DOMJUDGE_API_URL=" + cfg["api_url"],
        "--env", "JUDGEDAEMON_USERNAME=" + cfg["api_user"],
        "--env", "JUDGEDAEMON_PASSWORD_FILE=/run/xcpc-secrets/password",
        "--env", "CONTAINER_TIMEZONE=" + cfg["timezone"],
        "--env", "DAEMON_ID=" + str(cpu),
        "--env", "RUN_USER_UID_GID=" + str(uid_for(cpu)), IMAGE]


def fresh_heartbeat(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        value = float(value)
        return math.isfinite(value) and -5 <= time.time() - value <= 120
    except (TypeError, ValueError):
        return False


def install(rt, cfg, args, ask):
    root = Path(__file__).parent / "docker"
    rt.run(["python3", root / "preflight.py"])
    online_raw = Path("/sys/devices/system/cpu/online").read_text().strip()
    online = set()
    for segment in online_raw.split(","):
        limits = list(map(int, segment.split("-")))
        online.update(range(limits[0], limits[-1] + 1))
    if not set(cfg["cpus"]).issubset(online):
        raise InstallError(f"CPU 不在线；有效 CPU：{online_raw}")
    if any(len(cfg["hostname"] + "-" + str(cpu)) > 64 for cpu in cfg["cpus"]):
        raise InstallError("注册主机名连同 CPU 后缀不得超过 64 字符。")
    # Avoid sharing sandbox UIDs with host users or another container in this fleet.
    for cpu in cfg["cpus"]:
        for getter in [pwd.getpwuid, grp.getgrgid]:
            try:
                getter(uid_for(cpu))
            except KeyError:
                continue
            raise InstallError(f"宿主机 UID/GID {uid_for(cpu)} 已使用，不能作为沙箱身份。")
    path = CONFIG / "secrets/password"
    if getattr(args, "_api_password", None):
        password = args._api_password
    elif args.api_password_file:
        password = Path(args.api_password_file).read_text().rstrip("\r\n")
    elif path.exists():
        password = path.read_text().rstrip("\r\n")
    elif args.yes:
        raise InstallError("首次非交互安装须提供 --api-password-file。")
    else:
        password = ask("judgehost API 密码（不是 SSH/admin 密码）", secret=True)
    if not password or not re.fullmatch(r"\S+", password):
        raise InstallError("API 密码不能为空或包含空白。")
    ca = args.api_ca_file or (str(CONFIG / "secrets/api-ca.crt")
                              if (CONFIG / "secrets/api-ca.crt").exists() else None)
    if request(cfg["api_url"] + "info", ca=ca)["provider"]["version"] != "9.0.1":
        raise InstallError("DOMserver 版本不是 9.0.1。")
    identity = request(cfg["api_url"] + "user", cfg["api_user"], password, ca=ca)
    roles = set(identity.get("roles", []))
    if "judgehost" not in roles or roles & {"admin", "jury"}:
        raise InstallError("必须使用专用 judgehost 角色账号。")
    registered = request(cfg["api_url"] + "judgehosts", cfg["api_user"], password, ca=ca)
    if not (CONFIG / "config.json").exists() and any(
        r["hostname"] in {cfg["hostname"] + "-" + str(cpu) for cpu in cfg["cpus"]} for r in registered
    ):
        raise InstallError("主站已有同名评测进程；请使用新名称或先退役旧机器。")

    def packages():
        rt.run(["apt-get", "update"])
        packages = ["chrony", "ca-certificates", "curl"]
        if not shutil.which("docker"):
            packages.append("docker.io")
        rt.run(["apt-get", "-o", "Dpkg::Lock::Timeout=300", "install", "-y"] + packages)
        if rt.state["os"] == "ubuntu":
            rt.run(["apt-get", "remove", "-y", "apport"])
        rt.run(["systemctl", "enable", "--now", "docker", "chrony"])
        rt.run(["timedatectl", "set-timezone", cfg["timezone"]])
        rt.run(["chronyc", "waitsync", "12", "0.5", "0", "5"], timeout=75)
    rt.step("Docker Engine 与时间同步", packages)
    info = json.loads(rt.run(DOCKER + ["info", "--format", "{{json .}}"], capture=True))
    if info.get("OSType") != "linux" or info.get("CgroupVersion") != "2":
        raise InstallError("Docker 必须是本机 Linux rootful 引擎，使用 cgroup v2。")
    if any("rootless" in option for option in info.get("SecurityOptions", [])):
        raise InstallError("judgehost 不支持本方案中的 rootless Docker。")

    build_dir = mkdir(CACHE / "judgehost-image", 0o700)
    recipe = hashlib.sha256(b"".join((root / name).read_bytes() for name in ["Dockerfile", "start.sh"])).hexdigest()
    image = inspect("image", IMAGE)
    if image and (image["Config"].get("Labels") or {}).get("org.xcpc.recipe") != recipe:
        raise InstallError("已有同名但配方不同的镜像；本版不自动升级或覆盖它。")
    if not image:
        print("构建 Docker judgehost 9.0.1（官方基镜像 + 固定源码）…", flush=True)
        for name in ["Dockerfile", "start.sh"]:
            shutil.copyfile(root / name, build_dir / name)
        shutil.copyfile(rt.artifact("domjudge"), build_dir / "domjudge-9.0.1.tar.gz")
        rt.run(DOCKER + ["pull", "--platform", "linux/amd64", BASE_IMAGE], timeout=3600)
        rt.run(DOCKER + ["build", "--platform", "linux/amd64", "--label", "org.xcpc.recipe=" + recipe,
                        "--tag", IMAGE, build_dir], timeout=3600)
        image = inspect("image", IMAGE)
    version = rt.run(DOCKER + ["run", "--rm", "--entrypoint", "/opt/domjudge/judgehost/bin/runguard",
                               IMAGE, "--version"], capture=True)
    if "9.0.1/" not in version:
        raise InstallError("容器内 runguard 不是 9.0.1，停止。")
    cfg["image_id"] = image["Id"]
    rt.save()
    mkdir(CONFIG, 0o700)
    mkdir(CONFIG / "secrets", 0o700)
    # Record a pending restart before replacing a file seen through a directory mount.
    if path.exists() and path.read_text().rstrip("\r\n") != password:
        rt.state["judgehost_restart_pending"] = True
        rt.save()
    write(path, password + "\n", 0o600)
    if args.api_ca_file:
        ca_content = Path(args.api_ca_file).read_text()
        if "PRIVATE KEY" in ca_content or "BEGIN CERTIFICATE" not in ca_content:
            raise InstallError("--api-ca-file 必须是公开 CA 证书，不是私钥。")
        rt.state["judgehost_restart_pending"] = True
        rt.save()
        write(CONFIG / "secrets/api-ca.crt", ca_content, 0o644)
    write(CONFIG / "config.json", json.dumps(cfg, indent=2) + "\n", 0o600)
    for cpu in cfg["cpus"]:
        name = container_name(cpu)
        current = inspect("container", name)
        if current:
            labels = current["Config"].get("Labels") or {}
            if (labels.get("org.xcpc.owner") != OWNER or labels.get("org.xcpc.spec") != spec_hash(cfg, cpu)
                    or current["Image"] != cfg["image_id"]):
                raise InstallError(f"同名容器 {name} 不是此配置的受管理实例，不覆盖。")
            if rt.state.get("judgehost_restart_pending"):
                rt.run(DOCKER + ["restart", name], timeout=240)
            elif not current["State"]["Running"]:
                rt.run(DOCKER + ["start", name])
        else:
            for suffix in ["logs", "judgings"]:
                volume = name + "-" + suffix
                existing = inspect("volume", volume)
                if existing and (existing.get("Labels") or {}).get("org.xcpc.owner") != OWNER:
                    raise InstallError(f"存在非本工具管理的数据卷 {volume}，不接管。")
                rt.run(DOCKER + ["volume", "create", "--label", "org.xcpc.owner=" + OWNER, volume])
            rt.run(run_args(cfg, cpu))
    mkdir("/root/contest", 0o700)
    mkdir("/root/contest/judgehost", 0o700)
    link(CONFIG / "config.json", "/root/contest/judgehost/config.json")
    link(CONFIG / "secrets/password", "/root/contest/judgehost/api-password")
    write("/root/contest/README.md",
          "# Docker 评测机\n\n配置：judgehost/config.json；密码：judgehost/api-password（root 专用）。\n"
          "检查：xcpc-check；日志：docker logs xcpc-judgehost-CPU编号。\n"
          "改密后需重启容器；维护前在主站禁用并等待当前评测完成。\n"
          "不要直接修改 CPU/主机名；不要删除 judgings 数据卷。\n"
          "完整说明：/opt/xcpc-installer/docs/OPERATIONS.md\n", 0o600)


def check():
    cfg = json.loads((CONFIG / "config.json").read_text())
    password = (CONFIG / "secrets/password").read_text().strip()
    ca = CONFIG / "secrets/api-ca.crt"
    ca = ca if ca.exists() else None
    for cpu in cfg["cpus"]:
        name = container_name(cpu)
        current = inspect("container", name)
        if not current or not current["State"]["Running"]:
            raise InstallError(f"{name} 未运行；查看 docker logs {name}")
        if (current["Image"] != cfg["image_id"]
                or current["HostConfig"]["RestartPolicy"]["Name"] != "unless-stopped"
                or not current["HostConfig"]["Privileged"]
                or current["HostConfig"].get("CgroupnsMode") != "host"
                or current["HostConfig"].get("CpusetCpus") != str(cpu)):
            raise InstallError(f"{name} 运行配置与记录不一致。")
        print(f"PASS Docker：{name}，CPU {cpu}，镜像版本 9.0.1", flush=True)
    def heartbeat():
        records = request(cfg["api_url"] + "judgehosts", cfg["api_user"], password, ca=ca)
        lookup = {r["hostname"]: r for r in records}
        for cpu in cfg["cpus"]:
            name = cfg["hostname"] + "-" + str(cpu)
            item = lookup.get(name, {})
            if item.get("enabled") is not True or not fresh_heartbeat(item.get("polltime")):
                raise InstallError(f"{name} 尚未注册、被禁用或心跳超时。")
    wait_for("Docker 评测机注册与心跳", heartbeat, 120)
    print("OK：Docker 评测机已上线。仍需用真实测试提交验证各语言和判题结果。", flush=True)
