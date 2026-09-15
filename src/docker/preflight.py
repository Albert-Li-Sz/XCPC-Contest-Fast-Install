#!/usr/bin/python3
"""Read-only host checks. Never change GRUB, mounts or cgroup controllers."""
import json
import os
import platform
import re
import subprocess
from pathlib import Path


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


def cgroup_status(mountinfo, controllers):
    """Classify actual mounts; a process at '/' is not evidence of a container."""
    mounts = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or not filesystem:
            continue
        target, kind = fields[4], filesystem[0]
        if (target == "/sys/fs/cgroup" or target.startswith("/sys/fs/cgroup/")) and kind in {"cgroup", "cgroup2"}:
            mounts.append({"target": target, "filesystem": kind})
    v1 = any(m["filesystem"] == "cgroup" for m in mounts)
    v2 = [m["target"] for m in mounts if m["filesystem"] == "cgroup2"]
    if v1 and v2:
        mode = "hybrid"
    elif "/sys/fs/cgroup" in v2:
        mode = "v2"
    elif v1:
        mode = "v1"
    elif v2:
        mode = "v2-nonstandard"
    else:
        mode = "unavailable"
    errors = []
    if mode == "hybrid":
        errors.append("检测到 cgroup v1/v2 混合模式；需要 /sys/fs/cgroup 统一使用 cgroup v2，不能仅凭 unified 子目录判定已满足要求。")
    elif mode != "v2":
        errors.append(f"当前 cgroup 模式为 {mode}；需要在 /sys/fs/cgroup 挂载完整 cgroup v2。")
    elif controllers is None:
        errors.append("cgroup v2 已挂载，但无法读取 /sys/fs/cgroup/cgroup.controllers。")
    elif not {"memory", "cpuset"}.issubset(controllers.split()):
        errors.append("cgroup v2 缺少 memory/cpuset 控制器；检查控制器是否被 v1 占用或未向当前环境开放。")
    return {"mode": mode, "mounts": mounts, "controllers": (controllers or "").split()}, errors


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
    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    try:
        content = controllers.read_text()
    except OSError:
        content = None
    cgroups, issues = cgroup_status(Path("/proc/self/mountinfo").read_text(), content)
    errors.extend(issues)
    kernel = platform.release()
    match = re.match(r"(\d+)\.(\d+)", kernel)
    if not match or tuple(map(int, match.groups())) < (5, 19):
        errors.append("需要 Linux 内核 5.19 或更新版本。")
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
