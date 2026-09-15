#!/usr/bin/env bash
# GENERATED main.sh contains the complete payload. Do not hand-edit generated code.
set -Eeuo pipefail
xcpc_main() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            -h|--help)
                cat <<'HELP'
XCPC 快速部署 1.2.1
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
主站/评测目标支持 Debian 13 / Ubuntu 24.04，amd64，root，systemd。
批量控制端支持具有 Python 3.11+ 和 OpenSSH 的 Linux/macOS。
不修改 GRUB/sysctl，不自动重启；已有部署可从菜单继续或检查。

HELP
                return 0 ;;
            --version) echo 'XCPC Fast Install 1.2.1'; return 0 ;;
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
        # shellcheck disable=SC1091
        . /etc/os-release
        case "$ID:$VERSION_ID" in
            debian:13|ubuntu:24.04) ;;
            *) echo '请先安装 Python 3.11 或以上，再运行交互入口。' >&2; return 1 ;;
        esac
        echo '准备入口依赖：Python 3（随后显示交互菜单）。' 
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates
    fi
    umask 077
    XCPC_TMP="$(mktemp -d /tmp/xcpc-installer.XXXXXXXX)"
    trap 'rm -rf -- "$XCPC_TMP"' EXIT
    base64 -d > "$XCPC_TMP/payload.tar.gz" <<'XCPC_PAYLOAD'
@@PAYLOAD@@
XCPC_PAYLOAD
    python3 - "$XCPC_TMP/payload.tar.gz" "@@PAYLOAD_SHA@@" <<'XCPC_VERIFY'
import hashlib, sys
with open(sys.argv[1], 'rb') as stream:
    actual = hashlib.sha256(stream.read()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('安装入口校验失败，请重新下载。')
XCPC_VERIFY
    tar -xzf "$XCPC_TMP/payload.tar.gz" --no-same-owner -C "$XCPC_TMP"
    python3 "$XCPC_TMP/installer.py" "$@"
}
# The call is after the complete function/payload, so a truncated download cannot install.
xcpc_main "$@"
