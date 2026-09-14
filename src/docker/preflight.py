#!/usr/bin/python3
"""Read-only checks. No GRUB/sysctl edits or cgroup writes."""
import json
import os
import platform
import re
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

def main():
    errors = []
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    if (release.get("ID"), release.get("VERSION_ID")) not in {("debian", "13"), ("ubuntu", "24.04")}:
        errors.append("Only Debian 13 and Ubuntu 24.04 are supported")
    if os.geteuid() != 0:
        errors.append("root privileges are required")
    if platform.machine() != "x86_64":
        errors.append("Only x86_64/amd64 is supported")
    if not Path("/run/systemd/system").is_dir():
        errors.append("systemd must be PID 1")
    if Path("/opt/domjudge/domserver").exists():
        errors.append("Refusing to install judgehost on this DOMserver")
    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    if not controllers.exists():
        errors.append("cgroup v2 is required; kernel parameters will not be changed")
    elif not {"memory", "cpuset"}.issubset(set(controllers.read_text().split())):
        errors.append("memory and cpuset controllers must be available")
    version = tuple(map(int, re.match(r"(\d+)\.(\d+)", platform.release()).groups()))
    if version < (5, 19):
        errors.append("Kernel >= 5.19 is required")
    if any(line.strip().endswith(":/") for line in Path("/proc/self/cgroup").read_text().splitlines()):
        errors.append("Missing host cgroup hierarchy; use a full VM or bare metal")
    online = parse_cpus(Path("/sys/devices/system/cpu/online").read_text())
    print(json.dumps({"ok": not errors, "errors": errors, "online_cpus": online,
                      "os": release.get("ID"), "release": release.get("VERSION_ID"),
                      "kernel": platform.release()}))
    return 0 if not errors else 1

if __name__ == "__main__":
    raise SystemExit(main())

