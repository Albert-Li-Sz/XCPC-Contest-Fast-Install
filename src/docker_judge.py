"""Run the official latest judgehost image, with one managed container per CPU."""
import grp
import hashlib
import json
import math
import platform
from pathlib import Path
import pwd
import re
import subprocess
import time

import cgroups

from common import (InstallError, mkdir, write, link, request, wait_for)

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
IMAGE = "domjudge/judgehost:latest"
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
        "image": IMAGE, "startup": "official-with-optional-ca-v1",
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
        # Official startup appends api/v4 itself; supply the site root.
        "--env", "DOMSERVER_BASEURL=" + cfg["api_url"].removesuffix("api/"),
        "--env", "JUDGEDAEMON_USERNAME=" + cfg["api_user"],
        "--env", "JUDGEDAEMON_PASSWORD_FILE=/run/xcpc-secrets/password",
        "--env", "CONTAINER_TIMEZONE=" + cfg["timezone"],
        "--env", "DAEMON_ID=" + str(cpu),
        "--env", "RUN_USER_UID_GID=" + str(uid_for(cpu)), cfg.get("image_id", IMAGE),
        # Keep the upstream image and entrypoint. Only load an optional private CA
        # before executing the upstream command; no image build or custom daemon.
        "/bin/bash", "-ec",
        "if [ -f /run/xcpc-secrets/api-ca.crt ]; then "
        "install -m 0644 /run/xcpc-secrets/api-ca.crt /usr/local/share/ca-certificates/xcpc-api.crt; "
        "update-ca-certificates; fi; exec /scripts/start.sh"]


def prepare_image(rt, cfg):
    """Resolve latest once; retries must retain the already selected image."""
    if cfg.get("image_ref") == IMAGE and cfg.get("image_id"):
        image = inspect("image", cfg["image_id"])
        if not image:
            digest = cfg.get("image_digest")
            if not digest:
                raise InstallError("安装记录中的镜像已丢失；请从备份恢复后重试。")
            rt.run(DOCKER + ["pull", "--platform", "linux/amd64", digest], timeout=3600)
            image = inspect("image", cfg["image_id"])
            if not image:
                raise InstallError("恢复的镜像与安装记录不一致，停止。")
    else:
        # Legacy containers may still be judging. Migration requires retiring
        # those containers first, while retaining their named data volumes.
        if cfg.get("image_id") and any(inspect("container", container_name(cpu)) for cpu in cfg["cpus"]):
            raise InstallError("检测到旧版自建镜像的容器。请按运维说明在维护窗口迁移；不会覆盖运行中的评测机。")
        print("拉取官方 Docker 镜像 domjudge/judgehost:latest…", flush=True)
        rt.run(DOCKER + ["pull", "--platform", "linux/amd64", IMAGE], timeout=3600)
        image = inspect("image", IMAGE)
        if not image:
            raise InstallError("官方镜像拉取后不存在，停止。")
    version = rt.run(DOCKER + ["run", "--rm", "--entrypoint", "/opt/domjudge/judgehost/bin/runguard",
                               image["Id"], "--version"], capture=True)
    match = re.search(r"DOMjudge version ([^\s]+)", version)
    if not match:
        raise InstallError("无法读取官方镜像中的评测程序版本。")
    cfg.update(image_ref=IMAGE, image_id=image["Id"], judgehost_version=match[1],
               image_digest=next((d for d in image.get("RepoDigests", [])
                                  if d.startswith("domjudge/judgehost@sha256:")), cfg.get("image_digest")))
    rt.save()
    print(f"官方镜像：{IMAGE}；评测程序：{cfg['judgehost_version']}；DOMserver：9.0.1", flush=True)


def fresh_heartbeat(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        value = float(value)
        return math.isfinite(value) and -5 <= time.time() - value <= 120
    except (TypeError, ValueError):
        return False


def preflight(rt):
    result = subprocess.run(["python3", str(Path(__file__).parent / "docker/preflight.py")],
                            capture_output=True, text=True, timeout=30, check=False)
    with rt.log.open("a") as log:
        log.write(result.stdout + result.stderr)
    try:
        report = json.loads(result.stdout)
        errors = report["errors"]
        if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
            raise ValueError("Malformed preflight errors")
        ok = report["ok"] is True
        selected = report.get("cgroup")
        if ok and (not isinstance(selected, dict) or selected.get("version") not in {"1", "2"}
                   or selected.get("mode") not in {"v1", "v2", "hybrid"}):
            raise ValueError("Missing cgroup selection")
    except (ValueError, KeyError, TypeError):
        raise InstallError(f"环境检查未返回有效诊断；查看私有日志 {rt.log}") from None
    if result.returncode or not ok or errors:
        detail = "\n".join("  - " + error for error in errors) or "  - 环境检查进程异常退出。"
        raise InstallError("评测机环境检查未通过：\n" + detail +
                           "\n安装已停止，本次尚未执行 Docker 安装或创建评测容器；不会修改 GRUB/sysctl 或自动重启。"
                           "\n请检查：findmnt -R /sys/fs/cgroup -o TARGET,FSTYPE,OPTIONS"
                           "\n完整诊断：" + str(rt.log))
    selected = report["cgroup"]
    print(f"cgroup 自动识别：{selected['mode']} → 使用 v{selected['version']}。", flush=True)
    return selected


def validate_engine(info, selected):
    if info.get("OSType") != "linux" or any("rootless" in option for option in info.get("SecurityOptions", [])):
        raise InstallError("Docker 必须是本机 Linux rootful 引擎。")
    version = str(info.get("CgroupVersion", ""))
    if version not in {"1", "2"}:
        raise InstallError("Docker 未报告有效的 cgroup 版本，请检查本机 Engine。")
    if version != selected["version"]:
        raise InstallError(f"Docker 使用 cgroup v{version}，宿主机挂载选择 v{selected['version']}；请检查引擎和挂载，脚本不会自动切换系统模式。")
    return version


def check_container_cgroup(name, version):
    # Read the container's own mounts: do not assume a host bind was effective.
    script = """if [ "$(stat -f -c %T /sys/fs/cgroup)" = cgroup2fs ]; then
    echo 2
else
    test -r /sys/fs/cgroup/memory/memory.memsw.limit_in_bytes
    test -r /sys/fs/cgroup/memory/memory.memsw.max_usage_in_bytes
    test -r /sys/fs/cgroup/cpuacct/cpuacct.usage
    test -n "$(cat /sys/fs/cgroup/cpuset/cpuset.cpus)"
    test -n "$(cat /sys/fs/cgroup/cpuset/cpuset.mems)"
    echo 1
fi"""
    result = subprocess.run(DOCKER + ["exec", name, "/bin/sh", "-ec", script],
                            capture_output=True, text=True, timeout=20, check=False)
    if result.returncode or result.stdout.strip() != version:
        raise InstallError(f"{name} 容器内的 cgroup 层级与宿主机 v{version} 不符或缺少控制器文件；检查可写挂载及容器日志。")


def install(rt, cfg, args, ask):
    selected = preflight(rt)
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

    from judge_dependencies import prepare
    rt.step("Docker Engine 与时间同步", lambda: prepare(rt, cfg))
    info = json.loads(rt.run(DOCKER + ["info", "--format", "{{json .}}"], capture=True))
    cfg["cgroup_version"] = validate_engine(info, selected)
    cfg["cgroup_mode"] = selected["mode"]
    rt.save()

    prepare_image(rt, cfg)
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
          "镜像：官方 domjudge/judgehost:latest；cgroup v1/v2 自动识别；本次结果、镜像版本和摘要见 config.json。\n"
          "检查：xcpc-check；日志：docker logs xcpc-judgehost-CPU编号。\n"
          "改密后需重启容器；维护前在主站禁用并等待当前评测完成。\n"
          "不要直接修改 CPU/主机名；不要删除 judgings 数据卷。\n"
          "完整说明：/opt/xcpc-installer/docs/OPERATIONS.md\n", 0o600)


def check():
    selected, errors = cgroups.detect()
    errors += cgroups.kernel_errors(selected, platform.release())
    if errors:
        raise InstallError("cgroup 自动检查失败：\n  - " + "\n  - ".join(errors))
    result = subprocess.run(DOCKER + ["info", "--format", "{{json .}}"],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise InstallError("无法读取本机 Docker Engine 状态。")
    version = validate_engine(json.loads(result.stdout), selected)
    print(f"PASS Docker 引擎：自动识别 cgroup v{version}（{selected['mode']}）", flush=True)
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
        check_container_cgroup(name, version)
        print(f"PASS Docker：{name}，CPU {cpu}，评测程序 {cfg.get('judgehost_version', '未记录')}，镜像 {cfg.get('image_ref', '旧版')}", flush=True)
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
