#!/usr/bin/python3
"""Read-only host checks. Never change GRUB, mounts or cgroup controllers."""
import json
import os
import platform
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cgroups import cgroup_status, detect, kernel_errors


def parse_cpus(text):
    result = set()
    for token in text.strip().split(","):
        if not token:
            continue
        bounds = token.split("-")
        if len(bounds) == 1:
            result.add(int(bounds[0]))
        elif len(bounds) == 2:
            start, end = map(int, bounds)
            if start > end:
                raise ValueError("CPU range is reversed")
            result.update(range(start, end + 1))
        else:
            raise ValueError("Invalid CPU range")
    return sorted(result)


def check_host():
    errors = []
    release = {"ID": "linux", "VERSION_ID": "unknown"}
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    if os.geteuid() != 0:
        errors.append("需要以 root 运行。")
    if platform.machine() != "x86_64":
        errors.append("仅支持 x86_64/amd64。")
    if not Path("/run/systemd/system").is_dir():
        errors.append("需要由 systemd 启动的完整 Linux 主机。")
    if Path("/opt/domjudge/domserver").exists():
        errors.append("不能在 DOMserver 主站上安装评测机。")
    cgroups, issues = detect()
    errors.extend(issues)
    kernel = platform.release()
    errors.extend(kernel_errors(cgroups, kernel))
    # Only explicit container detection excludes containers. Ordinary v1
    # controller paths ending in ':/' and a host's '0::/' are both valid.
    container = "unknown"
    try:
        detection = subprocess.run(["systemd-detect-virt", "--container"],
                                   capture_output=True, text=True, timeout=10, check=False)
        if detection.returncode == 0 and detection.stdout.strip() not in {"", "none"}:
            container = detection.stdout.strip()
            errors.append(f"检测到容器环境 {container}；请在完整 VM 或物理宿主机部署。")
        elif detection.returncode == 1:
            container = "none"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"ok": not errors, "errors": errors,
            "online_cpus": parse_cpus(Path("/sys/devices/system/cpu/online").read_text()),
            "os": release.get("ID"), "release": release.get("VERSION_ID"),
            "kernel": kernel, "cgroup": cgroups, "container": container}


def main():
    report = check_host()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
