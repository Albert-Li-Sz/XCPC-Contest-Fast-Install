# N 台 Docker 评测机 · 交互部署

推荐运行仓库的一行入口，选择 **3：通过 SSH 批量部署 N 台 Docker 评测机**。控制端仅编排远程安装，不会被改成评测机。

~~~bash
bash <(curl -fsSL https://raw.githubusercontent.com/Albert-Li-Sz/XCPC-Contest-Fast-Install/main/main.sh)
~~~

如果已下载完整仓库，也可从仓库根目录直接启动批量向导：

~~~bash
bash batch/start.sh
~~~

不必手工编写 inventory，也不必记 Ansible 参数。

## 准备

控制端支持 Linux/macOS，需要 Python 3.11+ 与 OpenSSH 客户端。若已有 Ansible Core 2.19–2.21，会直接使用；否则确认部署后自动创建临时 Python 环境并安装兼容版本。Linux root 控制端可能安装 python3-venv；非 root 控制端应预先准备可用的 Python venv。

目标不限制 Linux 发行版名称和版本；需要 amd64、Python 3.11+、完整 systemd、rootful Docker 所需权限和 cgroup v2（memory/cpuset）。缺少 Python 时按目标机的 APT、DNF/YUM、Zypper 或 Pacman 安装；已有 Python 版本过旧时需先更新。先准备主站 judgehost API 账号；确保控制端能 SSH 连接目标，目标能连接主站 API、Docker Hub、系统软件源和 NTP。

## 向导步骤

1. 填写 DOMjudge 主站地址、API 账号及隐藏输入的 API 密码。
2. 输入机器数量 N。
3. 填写共用 SSH 用户、端口，选择密钥/ssh-agent 或密码登录。密码模式可选择全部相同或每台独立输入。
4. 非 root 登录时，指定是否需要 sudo 密码；向导支持共用 sudo 密码或免密 sudo。
5. 填写同时部署数量，例如 N 为 10、同时部署为 3。
6. 需要时进入高级设置，修改时区或选择控制端上的私有 CA 公开证书。
7. 逐台填写 SSH IP/域名、唯一 judge_hostname 和 CPU。默认名称依次为 judge01、judge02；CPU 可填 1 或 1,3。
8. 核对中文摘要，选择开始、重新填写或退出。
9. 对首次连接的主机核对显示的 SSH 公钥指纹，确认后才发送登录凭据并部署。

同一轮使用共用 SSH 用户、端口和 sudo 方式；机器条件不同可以分组多次运行。重复 IP/域名字符串或重复评测名称会要求重新输入；不同域名可能指向同一 IP，请自行避免这种重复。远程 CPU 是否在线由目标安装器再次检查。

## 安装过程与结果

向导分发当前运行的**同一份自包含 main.sh**。即使使用进程替换，也能从已校验的内嵌包重建这份入口，不会在批量部署过程中改为下载另一个 main 版本。

每台目标机调用相同 Docker 安装实现，一颗选定 CPU 对应一个容器。Ansible 同步等待各机安装结果，目标端使用安装锁。首次拉取官方 `domjudge/judgehost:latest` 镜像可能较久，无需编译评测机源码，失败时看逐机汇总及目标 `/var/log/xcpc-installer/` 中的私有日志。

首次 SSH 主机密钥需要核对指纹。已有的用户 known_hosts 记录会沿用；本次新确认的密钥保存在本次临时文件中，不会自动写入用户全局 known_hosts。再次连接仍未知的主机时会再次询问。

## 密码与重试

密码不出现在配置摘要或进程参数中。控制端临时目录权限 700，清单/凭据文件权限 600；每台密码也只放在这个临时目录中。正常退出、失败或取消会清理临时环境。强制杀死进程或机器掉电可能留下临时目录，确认部署已停止后可清理对应 xcpc-batch 临时目录。

目标临时密码文件由 Ansible 清理；正式 API 密码保留在 `/etc/xcpc-judgehost/secrets/password`。不要公开包含私有配置的日志。

失败后重新运行向导，只填写需要重试的机器，保持原来的名称、CPU、地址和账号。已成功阶段会复用，已有判题数据卷不会因重试删除。中断时先检查目标端是否仍在安装，避免立即启动第二轮。

本目录的 deploy.yml 和 inventory.example.yml 保留作为可审阅的底层实现与参考；日常操作使用向导。部署完成后各机执行 `xcpc-check`，并提交真实语言测试程序。

详见 [完整运维手册](../docs/OPERATIONS.md)。
