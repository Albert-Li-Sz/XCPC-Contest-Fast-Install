# XCPC Contest Fast Install · DOMjudge 版

**直接运行，按中文菜单选择，全程交互填写，不需要记安装参数。**

~~~bash
bash <(curl -fsSL https://raw.githubusercontent.com/Albert-Li-Sz/XCPC-Contest-Fast-Install/main/main.sh)
~~~

在 SSH 终端内运行。主站和单机评测安装需要 root；批量部署时，运行脚本的机器作为控制端。

## 菜单

~~~text
XCPC 快速部署 · 中文交互菜单
  1) 安装主站套件：DOMjudge + CDS + Live（不含评测机）
  2) 安装本机 Docker 评测机
  3) 通过 SSH 批量部署 N 台 Docker 评测机
  4) 检查已安装服务
  5) 查看部署说明
  0) 退出
~~~

输入数字并回车。方括号显示默认值，直接回车即可使用；地址、名称或 CPU 格式错误时会提示重新填写。密码输入时不显示字符。最后会显示中文配置摘要，可以开始、重新填写或退出；确认前不执行应用部署。

如果已下载脚本，只需要：

~~~bash
bash main.sh
~~~

## 1. 安装主站

在空白主站上选择 **1**：

1. 填写供选手、裁判和直播客户端访问的 IPv4 或域名，例如 192.0.2.10。
2. 选择默认设置；需要改时区或使用本地发行包时进入高级设置。
3. 核对摘要，选择“确认，开始安装”。

自动安装 **DOMjudge 9.0.1、ICPC Tools CDS 2.6.1331、ICPC Live 3.5.0**，以及 MariaDB、Nginx、PHP-FPM、Java 21、chrony。主站不安装评测机。

| 应用 | 示例入口 |
| --- | --- |
| DOMjudge | http://192.0.2.10/ |
| CDS | https://192.0.2.10:8443/ |
| Live 管理后台 | https://192.0.2.10:8444/admin |
| Live overlay | https://192.0.2.10:8444/ |

示例 IP 需要换成实际地址。DOMjudge 使用根路径 `/`。CDS/Live 初始使用自签名证书，客户端需信任公开证书或替换为受信任证书。

脚本创建独立随机密码，配置 DOMjudge → CDS → Live 的数据链路，并在 `/root/contest` 按应用建立精简软链接和 Markdown 说明。初始密码位于 `/root/contest/initial-credentials.txt`，仅 root 可读；网页改密后该记录不会自动更新。

## 2. 安装一台 Docker 评测机

在另一台评测主机上选择 **2**：

1. 填写主站地址。可直接输入 `192.0.2.10`，向导会补全为 `http://192.0.2.10/api/`；HTTPS 可输入完整地址。
2. 填写唯一短主机名，例如 `judge01`。
3. 查看向导显示的在线 CPU，填写 `1`，或 `1,3` 等列表。
4. 选择默认设置，隐藏输入 judgehost API 密码。
5. 核对摘要，确认安装。

API 密码来自 DOMjudge 的 judgehost 角色账号，不是 SSH、admin、CDS 或 Live 密码。主站初始评测凭据可在私有终端查看 `/opt/domjudge/domserver/etc/restapi.secret`。自定义 API 用户或私有 CA 证书通过高级设置填写，无需准备密码文件或拼接参数。

选择 CPU `1,3` 会创建 `judge01-1` 和 `judge01-3` 两个评测实例，每个 CPU 一个 Docker 容器。CPU 从 0 编号，建议预留 CPU 0 给系统，避免同时使用同一物理核的超线程兄弟。

直接拉取并运行官方镜像 **`domjudge/judgehost:latest`**，无需构建镜像，也无需下载 judgehost 源码。使用[官方镜像的启动方式](https://hub.docker.com/r/domjudge/judgehost)，API 密码通过文件提供。

安装时显示并记录镜像实际版本和摘要；`latest` 是滚动标签，不等于固定的 9.0.1。2026-09-14 拉取到的评测程序为 9.0.0/release；主站仍安装 9.0.1。当前组合的验证结果见[测试记录](docs/TESTING.md)。重试沿用首次安装记录的镜像，不在比赛期间自动追随标签更新。

## 3. 一次部署 N 台 Docker 评测机

在能通过 SSH 访问各评测机的控制端运行相同入口，选择 **3**。

向导依次询问主站 API、API 密码、机器数量、SSH 用户和端口、密钥或密码登录、并发数量；然后逐台填写 SSH 地址、唯一评测名称和 CPU。支持所有机器共用 SSH 密码或每台分别输入，非 root 用户可设置 sudo 密码。

不必手工编辑清单，也不必写 Ansible 命令。脚本自动生成私有临时清单并分发当前同一份安装入口；合适的 Ansible 已安装时直接使用，否则确认部署后准备临时运行环境。

首次连接未知主机时会展示 SSH 公钥指纹供核对；不关闭主机密钥验证。执行结束清理控制端临时凭据，结果按主机显示。详见 [批量部署说明](batch/README.md)。

## 4. 检查与继续部署

再次运行同一入口即可：

- 选择 **4** 检查服务，也可直接执行 `xcpc-check`。
- 检测到已有安装时，对应安装选项会显示为“继续 / 重试已有部署”；不会提供在同机叠加另一角色的选项。
- 重试沿用已保存的地址、主机名、CPU 和比赛配置，跳过已完成阶段；比赛和用户资料仍在网页维护。
- 选择 **5** 查看 Markdown 说明，阅读器中按 q 返回菜单。

1.2.0 可读取本工具 1.0.0 / 1.1.0 的安装状态。主站可继续重试；已有自建镜像的评测机需按[迁移说明](docs/OPERATIONS.md#从旧版自建镜像迁移)在维护窗口切换，脚本不会直接覆盖旧容器。

## 高级设置和本地发行包

向导默认使用 Asia/Shanghai 时区、官方发行包下载地址和 judgehost API 账号。高级菜单可填写时区、主站已有发行包目录、自定义 API 账号或私有 CA 公开证书；主站首次接入比赛 ID 可留空使用官方示例。

主站的本地发行包目录准备以下原版文件，脚本仍会验证固定 SHA-256：

~~~text
domjudge-9.0.1.tar.gz
wlp.CDS-2.6.1331.zip
live-v3-3.5.0.jar
~~~

主站需要三个包；Docker 评测机不需要这些包，也不再询问发行包目录。评测机需要访问 APT 和 Docker Hub；批量部署同样直接拉取官方镜像。摘要见 [src/common.py](src/common.py)。

## 运行条件

| 项目 | 要求 |
| --- | --- |
| 主站/评测目标系统 | Debian 13 或 Ubuntu 24.04，amd64/x86_64，root，systemd |
| 主站参考资源 | 8 GiB RAM、4 vCPU、20 GiB 磁盘；至少约 4 GiB RAM、根分区 6 GiB 可用空间 |
| 评测宿主机 | 完整 VM 或物理机，内核 ≥5.19、cgroup v2、memory/cpuset 控制器 |
| Docker | 本机 rootful Engine；不支持 Docker Desktop、rootless 或受限 LXC 作为正式目标 |
| 批量控制端 | Linux/macOS，Python 3.11+、OpenSSH；无合适 Ansible 时需要联网准备临时环境 |
| 网络 | APT、官方发行包、Docker Hub、DNS、NTP；控制端可 SSH 连接评测机，评测机可访问主站 API |

**不修改 GRUB/sysctl，不自动重启。**Docker 评测容器使用 privileged、host cgroup namespace 和可写 cgroup 挂载，容器内运行官方 cgroup 初始化工具。

入口内含安装代码、向导、模板和说明，不会重新拉取其他版本的零散脚本。支持进程替换；下载截断或内嵌包校验失败时不会开始安装。入口版本可通过把 URL 中 `main` 替换为审核过的提交 SHA 固定。

首次缺少 Python 的受支持 Linux 主机，会先安装菜单所需的 Python 和 CA；其他控制端请预先安装 Python。本工具面向空白机器及自身安装的恢复，不接管已有数据库、应用目录或冲突站点。

## 说明与开发

- [完整部署与办赛运维说明](docs/OPERATIONS.md)
- [批量交互向导说明](batch/README.md)
- [测试记录与验证边界](docs/TESTING.md)

源码在 src，main.sh 为生成物。修改源码或文档后运行 `python3 scripts/build.py` 重新生成。自动化参数接口仅供内部批量任务和开发检查使用，日常安装直接使用菜单。

正式比赛前仍须验证实际语言判题、封榜/解榜、OBS 画面、重启恢复和容量。

## 上游与许可证

- [DOMjudge 9.0 官方手册](https://www.domjudge.org/docs/manual/9.0/index.html)
- [DOMjudge 官方 Docker 打包源码](https://github.com/DOMjudge/domjudge-packaging/tree/main/docker)
- [CDS 2.6.1331](https://github.com/icpctools/icpctools/releases/tag/v2.6.1331)
- [ICPC Live 3.5.0](https://github.com/icpc/live-v3/releases/tag/v3.5.0)

安装器采用 [MIT License](LICENSE)。上游程序保留各自许可证。
