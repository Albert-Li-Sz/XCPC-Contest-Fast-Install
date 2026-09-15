"""Prepare judgehost dependencies by capability, without a distro allowlist."""
from pathlib import Path
import re
import shutil
import subprocess

from common import InstallError


def rpm_docker_package(manager):
    for package in ["docker-ce", "moby-engine", "docker"]:
        result = subprocess.run([manager, "-q", "list", "--available", package],
                                capture_output=True, text=True, timeout=120, check=False)
        if result.returncode == 0 and re.search(r"^" + re.escape(package) + r"\.(?:x86_64|noarch)\s", result.stdout, re.M):
            return package
    raise InstallError("当前 RPM 软件源没有 Docker Engine 包；请先按 Docker 官方文档配置软件源或安装本机 Docker，再重试。")


def install_missing(rt):
    missing = [binary for binary in ["docker", "chronyc", "curl"] if not shutil.which(binary)]
    if not missing:
        return
    packages = ["ca-certificates"]
    if "chronyc" in missing:
        packages.append("chrony")
    if "curl" in missing:
        packages.append("curl")
    if shutil.which("apt-get"):
        if "docker" in missing:
            packages.append("docker.io")
        rt.run(["apt-get", "update"])
        rt.run(["apt-get", "-o", "Dpkg::Lock::Timeout=300", "install", "-y"] + packages)
    elif shutil.which("dnf") or shutil.which("yum"):
        manager = "dnf" if shutil.which("dnf") else "yum"
        if "docker" in missing:
            packages.append(rpm_docker_package(manager))
        rt.run([manager, "install", "-y"] + packages)
    elif shutil.which("zypper"):
        if "docker" in missing:
            packages.append("docker")
        rt.run(["zypper", "--non-interactive", "refresh"])
        rt.run(["zypper", "--non-interactive", "install"] + packages)
    elif shutil.which("pacman"):
        if "docker" in missing:
            packages.append("docker")
        # Do not refresh only package databases or perform a full OS upgrade.
        rt.run(["pacman", "-S", "--needed", "--noconfirm"] + packages)
    else:
        raise InstallError("缺少依赖：" + ", ".join(missing) +
                           "；请用本系统包管理器安装 Docker Engine、chrony、curl 和 CA 证书后重试。")
    unresolved = [binary for binary in missing if not shutil.which(binary)]
    if unresolved:
        raise InstallError("依赖安装后仍缺少：" + ", ".join(unresolved))


def chrony_service():
    for name in ["chrony", "chronyd"]:
        if any((Path(directory) / (name + ".service")).is_file()
               for directory in ["/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"]):
            return name
    raise InstallError("缺少 chrony/chronyd 的 systemd 服务；请安装本系统的 chrony 服务包。")


def prepare(rt, cfg):
    install_missing(rt)
    # Ubuntu's crash handler conflicts with judgehost sandbox expectations.
    if shutil.which("apt-get") and shutil.which("apport-cli"):
        rt.run(["apt-get", "remove", "-y", "apport"])
    service = chrony_service()
    cfg["chrony_service"] = service
    rt.save()
    rt.run(["systemctl", "enable", "--now", "docker", service])
    rt.run(["timedatectl", "set-timezone", cfg["timezone"]])
    rt.run(["chronyc", "waitsync", "12", "0.5", "0", "5"], timeout=75)
