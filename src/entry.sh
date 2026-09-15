#!/usr/bin/env bash
# GENERATED main.sh contains the complete payload. Do not hand-edit generated code.
set -Eeuo pipefail
xcpc_main() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            -h|--help)
                cat <<'HELP'
XCPC 快速部署 1.3.0
直接运行即可进入中文交互菜单，无需追加安装参数：
  bash main.sh

菜单提供：
  1  安装主站：DOMjudge + CDS + Live
  2  安装本机 Docker 评测机
  3  通过 SSH 批量部署 N 台 Docker 评测机
  4  检查已安装服务
  5  查看部署说明
  0  退出

地址、CPU、账号、密码及高级设置均在向导中填写。
主站支持 Debian 12/13、Ubuntu 24.04/26.04；评测机不限制发行版版本。
安装目标仍须 Linux amd64、root、systemd；评测机还须完整 cgroup v2。
批量控制端支持具有 Python 3.11+ 和 OpenSSH 的 Linux/macOS。
不修改 GRUB/sysctl，不自动重启；已有部署可从菜单继续或检查。

HELP
                return 0 ;;
            --version) echo 'XCPC Fast Install 1.3.0'; return 0 ;;
        esac
    done
    if ! command -v python3 >/dev/null; then
        for arg in "$@"; do
            if [[ "$arg" == --plan || "$arg" == --check ]]; then
                echo '请先安装 python3；当前检查/预览未修改系统。' >&2; return 1
            fi
        done
        [[ "$(uname -s)" == Linux && "$(id -u)" == 0 ]] || {
            echo '请先安装 Python 3.11 或以上，然后重新运行交互入口。' >&2; return 1;
        }
        echo '准备入口依赖：Python 3（随后显示交互菜单）。'
        if command -v apt-get >/dev/null; then
            apt-get update
            DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates
        elif command -v dnf >/dev/null; then
            dnf install -y python3 ca-certificates
        elif command -v yum >/dev/null; then
            yum install -y python3 ca-certificates
        elif command -v zypper >/dev/null; then
            zypper --non-interactive install python3 ca-certificates
        elif command -v pacman >/dev/null; then
            pacman -S --needed --noconfirm python ca-certificates
        else
            echo '请先安装 Python 3.11 或以上，然后重新运行入口。' >&2; return 1
        fi
    fi
    python3 - <<'XCPC_PYTHON'
import sys
if sys.version_info < (3, 11):
    raise SystemExit('安装入口需要 Python 3.11 或以上；请配置兼容的 python3 后重试。')
XCPC_PYTHON
    umask 077
    XCPC_TMP="$(mktemp -d /tmp/xcpc-installer.XXXXXXXX)"
    trap 'rm -rf -- "$XCPC_TMP"' EXIT
    base64 -d > "$XCPC_TMP/payload.tar.gz" <<'XCPC_PAYLOAD'
@@PAYLOAD@@
XCPC_PAYLOAD
    python3 - "$XCPC_TMP/payload.tar.gz" "@@PAYLOAD_SHA@@" <<'XCPC_VERIFY'
import hashlib, shutil, sys, tarfile
from pathlib import Path
with open(sys.argv[1], 'rb') as stream:
    actual = hashlib.sha256(stream.read()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('安装入口校验失败，请重新下载。')
# The generated payload contains only regular files. Keep bootstrap extraction
# independent of distro tar versions and unavailable openat2 implementations.
base = Path(sys.argv[1]).parent.resolve()
with tarfile.open(sys.argv[1], 'r:gz') as bundle:
    for entry in bundle:
        name = Path(entry.name)
        target = base / name
        if not entry.isfile() or name.is_absolute() or '..' in name.parts:
            raise SystemExit('安装入口包含非法文件或路径。')
        if not target.resolve().is_relative_to(base):
            raise SystemExit('安装入口文件路径越界。')
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.extractfile(entry) as source, target.open('xb') as output:
            shutil.copyfileobj(source, output)
XCPC_VERIFY
    python3 "$XCPC_TMP/installer.py" "$@"
}
# The call is after the complete function/payload, so a truncated download cannot install.
xcpc_main "$@"
