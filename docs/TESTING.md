# 测试记录与复核方法

记录日期：2026-09-14。版本：安装器 1.1.0；DOMjudge 9.0.1；CDS 2.6.1331；Live 3.5.0。

## 已完成的验证

| 范围 | 结果 |
| --- | --- |
| 自包含 main.sh 与源码/文档一致性、嵌入包摘要 | 通过 |
| Bash 语法、ShellCheck | 通过 |
| Python 离线行为测试 | 29 项通过 |
| 中文交互向导 | 真实 TTY 中通过进程替换启动、无效选项重填、退出；密码不回显；批量向导填写后取消通过 |
| 单机/批量输入行为 | 错误地址与离线 CPU 重填、旧安装菜单、取消不部署、内嵌批量入口字节一致性通过 |
| Ansible 批量任务语法 | 通过；没有向清单中的示例地址发起连接 |
| Docker judgehost 镜像构建 | 成功，容器内 runguard 显示 9.0.1/release |
| DOMserver 官方源码构建、数据库初始化及根路径 API | 隔离 Ubuntu 24.04 amd64 环境通过 |
| DOMjudge → CDS → Live 数据链路 | 比赛元数据、队伍/题目同步、TLS、Live contestInfo 接口通过 |
| Docker 判题注册 | 两个容器分别使用 CPU 1/3、UID/GID 62861/62863，均注册成功 |
| 真实测试提交 | C、C++、Java、Python 3（PyPy）各 1 个 AC；C 的 WA、CE、TLE 样例符合预期 |

真实提交使用隔离测试站的官方 hello 示例题，未向用户已部署服务器提交测试数据。两个评测容器运行的镜像来源为仓库 Dockerfile 指定的官方基镜像摘要和 9.0.1 源码。

## 验证边界

本地测试宿主机为 Apple Silicon，Docker Desktop 模拟 amd64。主站容器的 systemd 子服务出现执行失败，因此主站组件联测由独立测试驱动启动 MariaDB、PHP-FPM、Nginx、CDS 和 Live；**没有把该测试算作原生 Linux 整机安装、systemd 开机恢复或 NTP 同步通过**。测试驱动没有包含在正式安装入口中，正式入口仍严格要求 systemd 和 chrony 验收。

Docker judgehost 的版本、启动、注册及上述真实判题结果来自实际运行，并非模拟 API。Docker Desktop 仅用于开发验证，不是本项目支持的正式评测宿主机。

Debian 13 分支、全新原生 amd64 主机上的完整一键流程、N 台真实 SSH 批量安装、断电/重启恢复、MLE 和各语言全部异常结果、长时间并发容量、OBS 画面、封榜/解榜均需要在实际比赛环境继续验收。仓库 CI 运行静态及离线检查，不自动连接用户服务器。

## 可重复执行的离线检查

在仓库根目录执行：

~~~bash
python3 scripts/build.py --check
bash -n main.sh
bash -n src/docker/start.sh
shellcheck src/entry.sh src/docker/start.sh batch/start.sh
python3 -B -m unittest discover -s tests -v
ansible-playbook -i batch/inventory.example.yml batch/deploy.yml \
  --syntax-check -e judge_api_password=syntax-only
~~~

用例覆盖参数注入、CPU 重复、失败阶段不标记完成、成功阶段不重复初始化数据库、归档越界、本地包校验、下载截断、自包含入口、Nginx 敏感路由保护、Docker CPU/沙箱身份、密码文件挂载、运行权限和心跳时效。

## 在全新 Linux VM 上验收

1. 准备两台独立 Debian 13 或 Ubuntu 24.04 amd64 VM。主站建议 8 GiB RAM；评测机确保完整 cgroup v2 与可用 CPU 1。先保存快照。
2. 主站以 root 运行 `bash main.sh`，选择菜单 1 并填写实际地址，等待 `xcpc-check` 全部通过。
3. 在私有终端查看主站初始密码及 judgehost API 凭据；在另一台 VM 选择菜单 2，填入 API URL、唯一主机名、CPU 编号和 API 密码。
4. 确认 Docker 镜像版本及主站 Judgehosts 页的注册、enabled 和心跳；每台运行 `xcpc-check`。
5. 在测试比赛中提交各语言 AC/WA/CE/TLE/MLE 样例，核对主站、CDS、Live 三端结果。
6. 在维护窗口测试重启：Docker、chrony、主站 systemd 服务应恢复，评测机重新产生心跳；再进行封榜及 OBS 实机演练。
7. 通过菜单 3 的交互向导扩展到多台，按每台结果确认，不把单台成功等同于全体成功。

不要在有比赛数据的主机上重复初始化数据库来测试“全新安装”；重试机制与全新安装验收是两种不同操作。

## 1.1.0 界面变更范围

本次新增完整中文单机/批量向导，修复非可寻址终端的输入方式，并使用跨平台 Python 校验入口。1.0.0 已有的 Docker 镜像、主站安装阶段与实际判题结果沿用前述验证；没有因界面改动重复宣称通过整机安装或 N 台真实 SSH 部署。新向导已在真实伪终端测试，输入与取消测试不会安装服务或连接示例主机。
