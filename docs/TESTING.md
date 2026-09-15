# 测试记录与复核方法

记录日期：2026-09-15。安装器 1.2.1；主站 DOMjudge 9.0.1；评测镜像为官方 `domjudge/judgehost:latest`；CDS 2.6.1331；Live 3.5.0。

## 当前验证记录

| 范围 | 结果 |
| --- | --- |
| 自包含 main.sh 与源码/文档一致性、嵌入包摘要 | 通过 |
| Bash 语法、ShellCheck | 通过 |
| Python 离线行为测试 | 43 项通过 |
| 中文交互向导 | 真实 TTY 进程替换启动、无效选项重填、密码不回显、取消不部署通过 |
| 批量部署 | 内嵌入口字节一致性、凭据临时文件清理、Ansible 语法通过；不连接示例主机 |
| 官方 latest 拉取及实际版本 | 9.0.0/release；直接使用官方镜像，未构建衍生镜像 |
| cgroup v2 | Docker Engine 报告版本 2；官方 create_cgroups 成功；离线检查验证 v1 被拒绝 |
| DOMserver 9.0.1 + 官方 judgehost 联测 | 两个容器注册成功，CPU 1/3、UID 62861/62863；容器内确认为 cgroup2fs |
| HTTP / 私有 CA 的 HTTPS API | 两条链路均完成注册和实际评测 |
| 真实测试提交 | C、C++、Java、Python 3（PyPy）各 1 个 AC；C 的 WA、CE、TLE 均符合预期 |

本次镜像摘要：`sha256:4c01f07e49023bcadd92255786372ec4c5fb5335bec5c9366f07b1fdddb28567`。这是本次拉取记录，源码仍使用 `latest` 标签。测试覆盖镜像实际版本记录、首次拉取、重试保留原镜像、按摘要恢复缺失镜像、旧容器不被自动覆盖、官方 API 根地址参数、密码文件目录挂载和 CPU 配置。

## 1.2.1 环境预检查修复

新增 9 项回归测试：复现 v1/v2 混合挂载并准确报错、允许宿主机根 cgroup 路径、继续拒绝纯 v1 / 缺失控制器 / 明确检测到的 LXC、拒绝仅有非标准位置的 v2 挂载、安装前直接显示详细诊断、正常报告继续执行，以及子进程输出异常时保留日志。完整离线测试共 43 项。

1.2.1 没有更换镜像、容器启动参数或判题程序；表中的真实提交、HTTP / HTTPS、双 CPU 判题与容器内 cgroup2fs 结果来自 1.2.0 的隔离联测，不将预检查修复表述为重新完成全部实机部署。此次也未替用户修改宿主机启动参数或重启服务器。

## 先前组件验证及本次边界

1.0.0 / 1.1.0 曾在隔离 Ubuntu 24.04 amd64 环境验证 DOMserver 源码构建、数据库初始化、网站根路径，以及 DOMjudge → CDS → Live 的比赛元数据、队伍、题目同步、TLS 与 Live contestInfo 接口。该数据链路代码在 1.2.0 中未改变；这些结果作为先前验证保留，不作为本次重复部署三套服务的记录。

先前自建 9.0.1 judgehost 的判题结果不能替代此次官方 latest 的验证。本次真实提交仅使用隔离测试站，不向用户已经部署的服务器提交测试数据。

本地宿主机为 Apple Silicon，Docker Desktop 模拟 amd64。测试驱动手动启动主站 MariaDB、PHP-FPM 和 Nginx，绕过 systemd / NTP；**不算作原生 Linux 整机安装、systemd 开机恢复或时间同步通过**。正式入口仍严格要求完整 Linux、systemd、cgroup v2 和 chrony 验收。Docker Desktop 仅用于开发验证，不是本项目支持的正式评测宿主机。

Debian 13 分支、全新原生 amd64 主机上的完整一键流程、N 台真实 SSH 批量安装、断电/重启恢复、MLE 和各语言全部异常结果、长时间并发容量、OBS 画面、封榜/解榜需要在实际比赛环境继续验收。仓库 CI 运行静态及离线检查，不连接用户服务器。未来 latest 的新版本需要重新进行语言与判题验收。

## 可重复执行的离线检查

在仓库根目录执行：

~~~bash
python3 scripts/build.py --check
bash -n main.sh
bash -n batch/start.sh
shellcheck src/entry.sh batch/start.sh
python3 -B -m unittest discover -s tests -v
ansible-playbook -i batch/inventory.example.yml batch/deploy.yml \
  --syntax-check -e judge_api_password=syntax-only
~~~

## 在全新 Linux VM 上验收

1. 准备两台独立 Debian 13 或 Ubuntu 24.04 amd64 VM。主站建议 8 GiB RAM；评测机确保完整 cgroup v2 与可用 CPU 1。先保存快照。
2. 主站以 root 运行 `bash main.sh`，选择菜单 1 并填写实际地址，等待 `xcpc-check` 全部通过。
3. 在私有终端查看主站 judgehost API 凭据；在另一台 VM 选择菜单 2，填写 API URL、唯一主机名、CPU 编号和 API 密码。
4. 核对 config.json 中的镜像实际版本、ID、摘要，确认 `docker info` 使用 cgroup v2，主站 Judgehosts 页显示启用状态和近期心跳，再运行 `xcpc-check`。
5. 在测试比赛中提交各语言 AC/WA/CE/TLE/MLE 样例，核对主站、CDS、Live 三端结果。
6. 在维护窗口测试重启：Docker、chrony、主站 systemd 服务应恢复，评测机重新产生心跳；再进行封榜及 OBS 实机演练。
7. 用菜单 3 扩展到多台，核对每台结果及镜像摘要；单台成功不等于全部机器成功。

不要在有比赛数据的主机上重新初始化数据库来测试全新安装。重试机制与全新安装验收应分别验证。
