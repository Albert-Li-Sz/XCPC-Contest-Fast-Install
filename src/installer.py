#!/usr/bin/env python3
"""Interactive entry point; validated inputs, exclusive lock, resumable phases."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ui
from ui import ask

from common import (VERSION, STATE_DIR, CACHE, INSTALL_ROOT, InstallError, Runtime,
                    host, api_url, cpu_list, mkdir, write)


def platform_check():
    if os.geteuid() != 0:
        raise InstallError("请以 root 运行。")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise InstallError("目标必须是 Linux amd64/x86_64。")
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    if (release.get("ID"), release.get("VERSION_ID")) not in {("debian", "13"), ("ubuntu", "24.04")}:
        raise InstallError("只支持 Debian 13 和 Ubuntu 24.04。")
    if not Path("/run/systemd/system").is_dir():
        raise InstallError("需要以 systemd 启动的完整 Linux 主机。")
    return release


def guessed_ip():
    try:
        values = subprocess.check_output(["hostname", "-I"], text=True).split()
        return next(v for v in values if "." in v and not v.startswith("127."))
    except (OSError, subprocess.CalledProcessError, StopIteration):
        return ""


def configuration(args, previous=None):
    if previous:
        if args.role and args.role != previous["config"]["role"]:
            raise InstallError("这台机器已有另一角色的安装状态；不能同时作为主站和评测机。")
        cfg = previous["config"]
        for attr, key in [("host", "host"), ("api_url", "api_url"), ("hostname", "hostname"),
                          ("timezone", "timezone"), ("contest_id", "contest_id"),
                          ("api_user", "api_user")]:
            value = getattr(args, attr)
            if value is not None and value != cfg.get(key):
                raise InstallError("不能通过重跑安装修改已有身份、地址或账号；请按运维说明维护。")
        if args.cpus and cpu_list(args.cpus) != cfg.get("cpus"):
            raise InstallError("不能通过重跑安装增删 CPU 实例。")
        return cfg
    role = args.role
    if role is None:
        raise InstallError("请先从菜单选择部署角色。")
    zone = args.timezone or "Asia/Shanghai"
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InstallError("时区名称无效。") from None
    cfg = {"role": role, "timezone": zone}
    if role == "server":
        address = args.host
        if not address:
            if args.yes:
                raise InstallError("自动安装主站必须指定 --host。")
            address = ask("比赛服务器供客户端访问的 IPv4 或域名", guessed_ip())
        cfg["host"] = host(address or "")
        ram = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())[1]) // 1024
        if ram < 3900:
            raise InstallError("主站套件至少需要 4 GiB RAM，建议 8 GiB 或以上。")
        cfg.update(db_buffer=min(2048, max(256, ram // 8)),
                   php_children=min(16, max(4, ram // 768)), contest_id=args.contest_id)
    else:
        values = [args.api_url, args.hostname, args.cpus]
        if args.yes and not all(values):
            raise InstallError("自动安装评测机须指定 --api-url、--hostname、--cpus。")
        cfg["api_url"] = api_url(args.api_url or ask("DOMjudge API 地址（以 /api/ 结尾）"))
        name = args.hostname or ask("唯一评测机短主机名", socket.gethostname().split(".")[0])
        if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
            raise InstallError("主机名需为小写字母开头的短名称，只含字母、数字和连字符。")
        cfg["hostname"] = name
        cfg["cpus"] = cpu_list(args.cpus or ask("评测 CPU 编号（从 0 开始，逗号分隔）", "1"))
        cfg["api_user"] = args.api_user or "judgehost"
        if not re.fullmatch(r"\S+", cfg["api_user"]):
            raise InstallError("API 用户名不能含空白。")
    return cfg



def main_menu(previous=None):
    options = []
    if previous:
        role = previous["config"]["role"]
        label = "主站套件" if role == "server" else "Docker 评测机"
        status = "已完成" if previous.get("complete") else "尚未完成"
        print(f"\n检测到本工具的{label}部署，状态：{status}。", flush=True)
        options.append(("1" if role == "server" else "2", "继续 / 重试已有" + label + "部署"))
    else:
        options += [("1", "安装主站套件：DOMjudge + CDS + Live（不含评测机）"),
                    ("2", "安装本机 Docker 评测机")]
    options += [("3", "通过 SSH 批量部署 N 台 Docker 评测机"),
                ("4", "检查已安装服务"), ("5", "查看部署说明"), ("0", "退出")]
    return ui.choose("XCPC 快速部署 · 中文交互菜单", options)


def show_manual():
    root = Path(__file__).parent
    file = root / "docs/OPERATIONS.md"
    if not file.is_file():
        file = root.parent / "docs/OPERATIONS.md"
    if shutil.which("less") and sys.stdout.isatty():
        print("按 q 返回菜单。", flush=True)
        subprocess.run(["less", str(file)], check=False)
    else:
        print(file.read_text(), flush=True)


def run_check():
    if not (INSTALL_ROOT / "health.py").is_file():
        raise InstallError("尚未安装本工具的服务；请先从菜单完成部署。")
    return subprocess.call(["python3", str(INSTALL_ROOT / "health.py")])


def interactive_settings(args, previous=None):
    if not previous:
        if args.role == "server":
            args.host = ui.field("主站供客户端访问的 IPv4 或域名", args.host or guessed_ip(), host)
        else:
            args.api_url = ui.field("主站地址（可直接填 IP，也可填完整 API URL）",
                                    args.api_url, ui.api_address)
            args.hostname = ui.field("这台评测机的唯一短主机名", args.hostname or "judge01", ui.judge_name)
            online = ui.online_cpus()
            default = next((cpu for cpu in sorted(online or {1}) if cpu > 0), 0)
            print("在线 CPU：" + (",".join(map(str, sorted(online))) if online else "请确认目标机 CPU 编号"), flush=True)
            print("CPU 从 0 编号；建议预留 CPU 0，多实例示例：1,3。", flush=True)
            args.cpus = ui.field("用于评测的 CPU 编号", args.cpus or str(default),
                                 lambda value: ui.cpus(value, online))
    else:
        # Identity stays fixed on retry, exactly as with the automated entry.
        configuration(args, previous)
        print("沿用已有地址、主机名、CPU 和比赛配置；不会重新初始化已完成的数据库。", flush=True)

    advanced = "高级设置：时区、发行包来源和比赛 ID" if args.role == "server" else "高级设置：时区、API 账号和证书"
    if ui.choose("其他设置", [("1", "使用默认设置 / 沿用已有设置"),
                               ("2", advanced)], "1") == "2":
        if not previous:
            args.timezone = ui.field("时区", args.timezone or "Asia/Shanghai", ui.timezone)
        if args.role == "server":
            source = ui.choose("应用发行包来源", [("1", "从官方地址下载"), ("2", "使用本机已有发行包目录")],
                               "2" if args.artifact_dir else "1")
            args.artifact_dir = (ui.field("发行包目录", args.artifact_dir, ui.directory) if source == "2" else None)
        if args.role == "judgehost":
            if not previous:
                args.api_user = ui.field("DOMjudge API 账号", args.api_user or "judgehost", ui.username)
            if ui.choose("API 证书信任", [("1", "使用系统信任 / 沿用已有证书"),
                                          ("2", "提供私有 CA 公开证书文件")], "1") == "2":
                args.api_ca_file = ui.field("CA 公开证书路径", args.api_ca_file, ui.existing_file)
        elif not previous:
            args.contest_id = ui.field("首次接入的比赛 ID（回车自动选择示例比赛）", args.contest_id, optional=True)
    cfg = configuration(args, previous)
    if cfg["role"] == "judgehost" and not args.api_password_file:
        existing = Path("/etc/xcpc-judgehost/secrets/password").is_file()
        supplied = bool(getattr(args, "_api_password", None))
        update = not (existing or supplied)
        if not update:
            update = ui.choose("评测机 API 密码", [("1", "沿用已有 / 刚才输入的密码"),
                                                  ("2", "输入新的密码")], "1") == "2"
        if update:
            args._api_password = ui.field(f"{cfg['api_user']} 的 API 密码（隐藏输入，不是 SSH/admin 密码）",
                                          validate=ui.token, secret=True)
    return cfg


def show_summary(cfg, args):
    print("\n请核对本次部署：", flush=True)
    if cfg["role"] == "server":
        print(f"  应用：DOMjudge 9.0.1 + CDS + Live\n  主站：http://{cfg['host']}/", flush=True)
    else:
        names = ", ".join(cfg['hostname'] + '-' + str(cpu) for cpu in cfg['cpus'])
        print(f"  应用：官方 Docker judgehost:latest\n  API：{cfg['api_url']}\n"
              f"  账号：{cfg['api_user']}（密码已隐藏）\n  评测实例：{names}", flush=True)
    print(f"  时区：{cfg['timezone']}", flush=True)
    if cfg["role"] == "server":
        print(f"  发行包：{args.artifact_dir or '从官方地址下载'}", flush=True)
    else:
        print("  镜像：domjudge/judgehost:latest（首次拉取，重试沿用已记录镜像）", flush=True)
    print("不修改 GRUB/sysctl，不自动重启系统。", flush=True)


def install_tools():
    origin = Path(__file__).parent
    mkdir(INSTALL_ROOT, 0o700)
    for entry in origin.iterdir():
        if entry.name == "__pycache__":
            continue
        destination = INSTALL_ROOT / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", ".ansible", "logs"))
        else:
            shutil.copyfile(entry, destination)
    docs = origin / 'docs' if (origin / 'docs').is_dir() else origin.parent / 'docs'
    if docs.is_dir():
        shutil.copytree(docs, INSTALL_ROOT / 'docs', dirs_exist_ok=True)
    write("/usr/local/sbin/xcpc-check",
          "#!/bin/sh\nexec /usr/bin/python3 /opt/xcpc-installer/health.py \"$@\"\n", 0o700)


def main():
    parser = argparse.ArgumentParser(description="安装 DOMjudge 主站套件或独立评测机")
    parser.add_argument("--role", choices=["server", "judgehost"])
    parser.add_argument("--host", help="主站 IPv4 或域名，不带协议和路径")
    parser.add_argument("--api-url")
    parser.add_argument("--api-user")
    parser.add_argument("--api-password-file")
    parser.add_argument("--api-ca-file", help="目标机上可信公开 CA 的路径")
    parser.add_argument("--hostname")
    parser.add_argument("--cpus")
    parser.add_argument("--timezone")
    parser.add_argument("--contest-id")
    parser.add_argument("--artifact-dir", help="主站三个原版发行包的本机目录")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--plan", action="store_true", help="只显示所选配置，不修改系统")
    parser.add_argument("--check", action="store_true", help="检查已安装服务")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if args.check:
        return run_check()
    state_path = STATE_DIR / "state.json"
    try:
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
    except PermissionError:
        # An unprivileged SSH control machine need not read a local root deployment.
        previous = None
    if args.role is None and not args.yes:
        while True:
            action = main_menu(previous)
            if action == "0":
                return 0
            if action == "5":
                show_manual()
                continue
            if action == "4":
                return run_check()
            if action == "3":
                from batch_wizard import main as batch_main
                return batch_main()
            args.role = {"1": "server", "2": "judgehost"}[action]
            break
    release = platform_check()
    if previous and previous.get("installer_version") not in {"1.0.0", "1.1.0", "1.2.0", VERSION}:
        raise InstallError("已有其他版本安装状态；本入口不执行自动升级。")
    while True:
        cfg = configuration(args, previous) if args.yes else interactive_settings(args, previous)
        show_summary(cfg, args)
        if args.plan:
            return 0
        if args.yes:
            break
        action = ui.choose("下一步", [("1", "确认，开始安装"), ("2", "重新填写"),
                                      ("0", "退出，不执行安装")])
        if action == "0":
            print("已退出，未执行安装。", flush=True)
            return 0
        if action == "1":
            break
    with open("/run/xcpc-installer.lock", "a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("已有安装进程运行。") from None
        if not previous:
            if cfg["role"] == "server":
                from server import fresh_server_check
                fresh_server_check()
            elif Path("/opt/domjudge/domserver").exists():
                raise InstallError("不能在 DOMserver 上部署评测机。")
            if shutil.disk_usage("/").free < 6 * 1024**3:
                raise InstallError("根文件系统至少需要 6 GiB 可用空间。")
        mkdir(STATE_DIR, 0o700)
        mkdir(CACHE, 0o700)
        mkdir("/var/log/xcpc-installer", 0o700)
        log = Path("/var/log/xcpc-installer") / (time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.log")
        write(log, "", 0o600)
        state = previous or {"installer_version": VERSION, "os": release["ID"],
                             "release": release["VERSION_ID"], "config": cfg, "done": []}
        if (state["os"], state["release"]) != (release["ID"], release["VERSION_ID"]):
            raise InstallError("发行版已变化；请使用专门的升级流程。")
        rt = Runtime(state, log, args.artifact_dir)
        rt.save()
        print(f"安装日志：{log}（仅 root 可读）", flush=True)
        if cfg["role"] == "server":
            from server import install
            install(rt, cfg)
        else:
            from docker_judge import install
            install(rt, cfg, args, ask)
        install_tools()
        from health import check
        check()
        state["judgehost_restart_pending"] = False
        state["installer_version"] = VERSION
        state["complete"] = True
        rt.save()
        if cfg["role"] == "server":
            print(f"\n安装完成：\nDOMjudge  http://{cfg['host']}/\n"
                  f"CDS       https://{cfg['host']}:8443/\n"
                  f"Live      https://{cfg['host']}:8444/admin\n"
                  "初始密码：/root/contest/initial-credentials.txt（root 专用）\n"
                  "运维入口：/root/contest/README.md\n检查命令：xcpc-check", flush=True)
        else:
            print("\n评测机部署完成。请在主站 Judgehosts 页面确认，并提交测试程序验收。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, ValueError, KeyError, subprocess.TimeoutExpired,
            subprocess.CalledProcessError) as error:
        print(f"\n错误：{error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\n已中断；不会删除现有数据。修复后可重新运行。", file=sys.stderr)
        raise SystemExit(130)
