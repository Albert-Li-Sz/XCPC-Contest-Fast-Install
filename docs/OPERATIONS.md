# DOMjudge、CDS、Live 部署与办赛运维

本文对应本仓库 1.4.0 安装器：DOMjudge 9.0.1、CDS 2.6.1331、Live 3.5.0。示例 IP 192.0.2.10 必须替换为实际主站地址。

## 交互安装入口

日常使用只运行这一条命令，不需要追加安装参数：

~~~bash
bash <(curl -fsSL https://raw.githubusercontent.com/Albert-Li-Sz/XCPC-Contest-Fast-Install/main/main.sh)
~~~

在 SSH 终端内操作。菜单 1 安装主站，菜单 2 安装本机 Docker 评测机，菜单 3 从控制端批量部署 N 台，菜单 4 检查服务，菜单 5 阅读本说明，0 退出。

主站向导填写 IP/域名即可使用默认配置；评测机向导填写主站地址、唯一主机名、CPU 和隐藏输入的 API 密码。主站地址可直接输入 IP，系统补全 API 路径。输入错误会提示重填；最后的摘要页面可选择开始、重新填写或退出。高级设置可改时区、使用已有发行包、指定 API 用户或私有 CA 证书。

再次运行时会识别已有安装，对应菜单改为继续/重试，沿用原有部署身份。1.4.0 可读取本工具 1.0.0 / 1.1.0 / 1.2.0 / 1.2.1 / 1.3.0 的状态；主站可继续重试，旧版评测机的镜像切换见第 7 节。

批量向导自动生成临时清单并询问 SSH 登录方式、密码及每台配置，无需手改 YAML；详情见[批量交互说明](../batch/README.md)。

## 1. 组件关系

~~~text
选手 / 裁判浏览器 ── HTTP 80 ── Nginx / PHP-FPM ── DOMjudge / MariaDB
                                     ↑
Docker 评测机 ── 主站 /api/ ──────────────┘

DOMjudge 指定比赛 /api/contests/ID
       │ cds-reader：api_reader + api_source_reader
       ▼
CDS :8443 ── live-reader：public ── Live 127.0.0.1:8081
                                         ↑
OBS / 管理浏览器 ── HTTPS :8444 ── Nginx 反代
~~~

CDS 和 Live 都不执行提交评测。安装主站后，如果没有评测机，提交会等待；这不表示主站部署失败。

首次选择官方数据库中的首个比赛作为演示源。真实比赛的时间、队伍、题目、语言和规则在 DOMjudge 网页管理。

## 2. 端口、域名和证书

| 端口 | 组件 | 访问范围 |
| --- | --- | --- |
| 22 | SSH | 运维、可选批量部署 |
| 80 | DOMjudge | 选手、裁判、评测机 |
| 3306 | MariaDB | 仅 127.0.0.1 |
| 8443 | CDS HTTPS | 展示客户端、运维；Live 从本机连接 |
| 8080 | CDS HTTP | 本方案禁用 |
| 8081 | Live 内部 HTTP | 仅 127.0.0.1 |
| 8444 | Live HTTPS | OBS、直播管理 |

脚本不更改防火墙或云安全组。按实际客户端所在网段放通需要的端口。域名需要提前解析到主机地址。

CDS/Live 初始证书是同一张自签名证书，包含填写的 IP/域名、127.0.0.1 和 localhost，有效期 825 天：

~~~text
/etc/icpc-cds/tls/cds.crt   公开证书，可向客户端分发
/etc/icpc-cds/tls/cds.key   私钥，仅 root
/etc/icpc-cds/tls/cds.p12   CDS 密钥库
/etc/icpc-live/truststore.jks  Live Java 信任库
~~~

只向客户端分发 cds.crt，不要分发 key、p12 或整个 /etc/icpc-cds。更新 CDS 证书时，同步更新 Live 信任库，并重启 CDS/Live、重新检查 Nginx。

主站初始使用 HTTP。需要公网 HTTPS 时，按自己的域名和证书方案配置 Nginx，并同步应用 BASEURL、judgehost API 地址和外部链接；本版不自动申请 ACME 证书。

## 3. 账号与密码

| 账号 | 所属系统 | 用途与权限 |
| --- | --- | --- |
| admin | DOMjudge | 网站管理员，官方生成 |
| judgehost | DOMjudge | 评测机 API，共享或按运维需要另建同角色账号 |
| cds-reader | DOMjudge | 只读数据与源码 API，给 CDS |
| admin | CDS | CDS 管理员 |
| presAdmin | CDS | Presentation 管理 |
| staff | CDS | 工作人员 |
| presentation | CDS | 公开展示 |
| balloon | CDS | 气球管理 |
| live-reader | CDS | 公开视图，给 Live |
| admin | Live | 预先确认的直播管理账号 |

各系统 admin 密码彼此独立，不使用 SSH 密码。主站初始密码文件：

~~~bash
cat /root/contest/initial-credentials.txt
~~~

只在私有终端查看。这个文件记录初始值，网页改密后不会更新。当前 CDS 账号由 /etc/icpc-cds/accounts.yaml 决定，Live 登录由 /var/lib/icpc-live/users.json 决定。

联动改密顺序：

1. DOMjudge judgehost 改密后，维护窗口更新各评测机 /etc/xcpc-judgehost/secrets/password，再重启对应 Docker 容器。文件只放密码；不要修改容器中自动生成的 restapi.secret。
2. DOMjudge cds-reader 改密后，同步 CDS cdsConfig.xml 的 ccs password，重启 icpc-cds。
3. CDS live-reader 改密后，同步 Live creds.json 中 cds_password，重启 CDS 和 Live。
4. DOMjudge、CDS、Live 管理员互不共用密码，各自修改。

本工具的 /var/lib/xcpc-installer/secrets.json 是首次配置的私有恢复记录，也不会随网页改密更新。不要把它复制到公共仓库、公开备份或工单。

## 4. 文件与目录

| 路径 | 内容 |
| --- | --- |
| /opt/domjudge/domserver | DOMjudge 正式程序 |
| /etc/xcpc-judgehost | 评测宿主机配置和私有凭据 |
| 容器内 /opt/domjudge/judgehost | 官方 latest 镜像自带的评测程序 |
| /opt/icpctools/cds-2.6.1331 | CDS 官方发行程序 |
| /opt/icpctools/cds | 指向上述版本的链接 |
| /etc/icpc-cds | CDS XML、账号、环境、JVM 和 TLS |
| /var/lib/icpc-cds | CDS 数据、缓存、Liberty 输出 |
| /opt/icpc-live/releases/3.5.0 | Live 官方 JAR |
| /opt/icpc-live/current | 当前版本链接 |
| /etc/icpc-live | Live 数据源、凭据、Java 信任库 |
| /var/lib/icpc-live | Live 账号、网页配置、预设、素材 |
| /opt/xcpc-installer | 已安装的检查代码及本说明 |
| /var/lib/xcpc-installer | 安装身份、阶段状态和私有初始配置 |
| /var/log/xcpc-installer | 每次运行的私有日志 |
| /var/cache/xcpc-installer | 下载包与 DOMjudge 构建树 |
| Docker xcpc-judgehost-CPU-judgings 卷 | 每个评测容器的评测工作目录 |
| Docker xcpc-judgehost-CPU-logs 卷 | 每个评测容器的 DOMjudge 日志 |
| 容器内 /chroot/domjudge | 预建语言环境 |

主站的 /root/contest：

~~~text
contest/
├── README.md
├── deployment.md -> /opt/xcpc-installer/docs/OPERATIONS.md
├── initial-credentials.txt             # root 专用，不能公开分享
├── domjudge/
│   └── php-fpm.conf -> 正式 FPM 配置
├── cds/
│   ├── cdsConfig.xml -> 正式 CDS 数据源配置
│   └── accounts.yaml -> 正式 CDS 账号配置
└── live/
    ├── settings.json -> 正式 Live 数据源配置
    └── creds.json -> 正式 Live 凭据配置
~~~

前台能修改的比赛、题目、用户、Live 视觉配置、素材、预设不建立软链接。软链接直接修改正式配置；编辑前备份真实目标，别误删目标文件。

## 5. 开赛前切换比赛

先在 DOMjudge 网页创建比赛并准备队伍、题目、语言、时间和规则。通过主站 API 确认其实际比赛 ID；不要把数据库中的内部数字 ID 与 API 的外部 ID 混淆。

在 /root/contest/cds/cdsConfig.xml 中，将 contest id 和 ccs url 的比赛 ID 同步更改，例如：

~~~xml
<contest id="regional2026" path="/var/lib/icpc-cds/contests/regional2026" recordReactions="false">
  <ccs url="http://127.0.0.1/api/contests/regional2026"
       user="cds-reader" password="保留当前实际密码" />
</contest>
~~~

为新比赛建独立数据目录，避免复用旧缓存：

~~~bash
install -d -o cds -g cds -m 750 /var/lib/icpc-cds/contests/regional2026
~~~

修改 Live settings.json 的 feeds[0].contestId 为 regional2026，保留 /api 数据源根地址及公开账号。随后：

~~~bash
systemctl restart icpc-cds
systemctl restart icpc-live
xcpc-check
~~~

不要通过重跑安装菜单改比赛；安装器的身份参数不会覆盖已部署配置。检查工具读取当前 CDS/Live 配置。多比赛、多数据源或自定义账号结构超出默认检查逻辑时，应相应维护检查脚本。

## 6. 服务管理与修改生效

~~~bash
systemctl status nginx mariadb icpc-cds icpc-live
journalctl -u icpc-cds -n 100 --no-pager
journalctl -u icpc-live -n 100 --no-pager
chronyc tracking
xcpc-check
~~~

主站支持以下系统，PHP 路径和服务名按安装后的实际版本自动生成：

| 主站系统 | 默认 PHP-FPM | CDS / Live Java |
| --- | --- | --- |
| Debian 12 | php8.2-fpm | OpenJDK 17 |
| Debian 13 | php8.4-fpm | OpenJDK 21 |
| Ubuntu 24.04 | php8.3-fpm | OpenJDK 21 |
| Ubuntu 26.04 | php8.5-fpm | OpenJDK 21 |

Java 使用系统软件源的对应包，CDS 的 `server.env`、Live 的服务启动路径与导入证书的 keytool 保持一致。固定的 CDS 2.6.1331 / Live 3.5.0 可运行于 Java 17；Ubuntu 26.04 不使用其默认 Java 25。PHP、Java 版本和 Java 路径记录在 `/var/lib/xcpc-installer/state.json` 中。

Debian 12 的 Python 3.11.2 尚无 TAR 解压过滤接口，安装器使用经过路径和符号链接校验的兼容解压实现；新版 Python 使用标准库 data filter。无需额外升级 Debian 12 的 Python。

| 修改项 | 生效操作 |
| --- | --- |
| DOMjudge FPM | php-fpm版本 -t 后 systemctl reload php版本-fpm |
| Nginx | nginx -t 后 systemctl reload nginx |
| CDS XML、账号、server.env、jvm.options | systemctl restart icpc-cds |
| Live settings.json、creds.json、信任库 | systemctl restart icpc-live |
| systemd unit | systemctl daemon-reload 后 restart 对应服务 |

维护重启前先安排窗口。主站用户数据和比赛文件不能用“重新初始化数据库”修复。

## 7. Docker 评测机部署与运维

### 安装和镜像

主站与评测机使用不同机器。评测目标不限制 Linux 发行版名称和版本，须为 amd64、Python 3.11+、systemd 启动的完整 VM 或物理机。cgroup v1/v2 根据当前挂载自动识别，无需在菜单中选择；v2 仍要求内核 ≥5.19，v1 不套用这一门槛（DOMjudge 要求 ≥3.2，另须满足所用 Docker Engine 的内核要求）。官方镜像的 create_cgroups 与 runguard 均包含 v1/v2 分支，安装器按同一根挂载规则选择。

安装器只检测当前模式，随后与本机 Docker 的 CgroupVersion 核对。不会修改 GRUB/sysctl、重挂载宿主机层级或自动重启；官方镜像启动时创建的评测 cgroup 属于当前已有模式。

菜单 2 和菜单 3 使用相同的依赖检查：已安装的 Docker、chrony、curl 直接沿用，缺少的依赖通过当前包管理器安装。始终连接本地 `/var/run/docker.sock`，要求本机 rootful Docker Engine；已有 Docker CLI 但没有可用本机 Engine 时，会给出错误。

| 包管理器 | 缺少 Docker 时的处理 |
| --- | --- |
| APT | 安装当前软件源的 docker.io |
| DNF / YUM | 在当前软件源依次查找 docker-ce、moby-engine、docker；未找到时提示先配置 Docker 官方源或自行安装 |
| Zypper | 安装当前软件源的 docker |
| Pacman | 安装当前软件库的 docker，不自动刷新数据库或执行全系统升级 |
| 其他 | 依赖已齐全则继续；否则提示手动安装缺少的依赖 |

自动识别 `chrony.service` 或 `chronyd.service`，启用 Docker 与对应时间服务并检查同步。RPM/SUSE/Arch 等分支有离线行为测试，尚未逐发行版整机验证。若软件源没有兼容包，请按对应系统的安装方法准备依赖后重试；脚本不擅自添加第三方软件源。

取消发行版白名单不取消沙箱运行条件：缺少所需控制器、只读挂载、rootless Docker、受限容器或 Docker 与宿主机模式不一致时会失败。完整的 v1 和具备所需 v1 控制器的混合模式可以通过。

直接使用 [DOMjudge 官方 Docker 镜像](https://hub.docker.com/r/domjudge/judgehost) `domjudge/judgehost:latest`。首次安装自动执行以下拉取操作，不下载评测源码、不执行 docker build：

~~~bash
docker pull domjudge/judgehost:latest
~~~

镜像提供 judgedaemon、runguard、chroot 和编译器环境，容器仍使用官方 `/scripts/start.sh` 启动评测。安装器把向导中的 `/api/` 地址转换成网站根地址，传入官方 `DOMSERVER_BASEURL`；官方启动脚本自行拼接 API 版本路径。密码通过 `JUDGEDAEMON_PASSWORD_FILE` 读取。仅在存在私有 CA 时，启动前将公开证书加入容器信任库，再执行官方启动脚本；没有生成或构建衍生镜像。

`latest` 会随官方发布变化。安装器显示 runguard 实际版本，把 `image_ref`、`image_id`、`image_digest`、`judgehost_version` 写入私有 config.json 与安装状态；创建容器时使用本次拉取解析出的镜像 ID，保证同一台机器的各 CPU 使用同一份镜像。重试使用该记录；镜像被清理时按记录摘要恢复，不重新选择 latest。摘要用于记录和恢复，不是代码中预设的版本锁定。

2026-09-14 官方 latest 的实际评测程序为 **9.0.0/release**，摘要为 `sha256:4c01f07e49023bcadd92255786372ec4c5fb5335bec5c9366f07b1fdddb28567`。主站保持 **9.0.1**。当前组合的实际验证见 TESTING.md；未来 latest 的版本、语言环境及与主站的兼容性不能由本次结果保证，安装后仍需真实提交验收。批量部署由各目标机分别拉取，完成后核对各机摘要，确保比赛使用一致的环境。

### 从旧版自建镜像迁移

全新评测机直接用菜单 2 或 3。只有已用本工具 1.0.0 / 1.1.0 安装过自建镜像的评测机需要以下维护步骤：

1. 在主站禁用该机器全部评测实例，等待当前评测完成。保存 `/etc/xcpc-judgehost`、`/var/lib/xcpc-installer` 和对应数据卷的备份。
2. 在目标机确认要迁移的容器名。使用 `docker stop xcpc-judgehost-1`、`docker rm xcpc-judgehost-1` 停止并移除相应旧容器；按实际 CPU 对每个容器执行。**不要加 `-v`，不要删除 judgings/logs 卷，不要清空安装状态或 API 密码。**
3. 运行新版一行入口，选择继续 / 重试已有评测机。地址、主机名、CPU、账号沿用旧记录；脚本拉取官方 latest 并使用原数据卷创建容器。只要旧容器仍存在，脚本就会停止迁移，不会覆盖它。
4. 确认新容器已启动后在主站重新启用对应评测实例，再执行 `xcpc-check` 并提交测试程序。若安装末尾因实例仍被禁用而心跳验收失败，重新启用后再次选择继续 / 重试即可。

脚本不在此次迁移中自动删除旧镜像或旧构建缓存，便于回退。需要回退时，在同一维护窗口停止并移除新容器、恢复备份的私有配置与安装状态，使用原版安装入口和保留的旧镜像恢复；不要恢复或初始化比赛数据库。

### 容器如何运行

在向导中填写主机名 `judge01`、CPU `1,3`，会创建：

| 宿主机容器名 | DOMjudge 注册名 | CPU | 沙箱 UID |
| --- | --- | --- | --- |
| xcpc-judgehost-1 | judge01-1 | 1 | 62861 |
| xcpc-judgehost-3 | judge01-3 | 3 | 62863 |

编号从 0 开始，用 `lscpu -e=CPU,CORE,SOCKET,ONLINE` 检查实际拓扑。建议预留 CPU 0 给系统，避免同时使用同一物理核的超线程兄弟。不同物理机的 judge_hostname 必须唯一。

容器为 privileged，使用 host 网络、host cgroup namespace 和可写 `/sys/fs/cgroup`；这是此判题沙箱方案运行所需的宿主机权限。容器不发布新网站端口。评测进程以 domjudge 运行，提交程序使用独立沙箱用户，UID 为 `62860 + CPU编号`，不会让不同 CPU 容器复用同一个沙箱 UID；官方启动脚本创建对应 GID 的 domjudge-run 组，而沙箱用户的主组是 nogroup。Docker 重启策略为 `unless-stopped`。

### 配置与密码

~~~text
/root/contest/
├── README.md
└── judgehost/
    ├── config.json -> /etc/xcpc-judgehost/config.json
    └── api-password -> /etc/xcpc-judgehost/secrets/password
~~~

config.json 不包含 API 密码；用于记录不可随意修改的身份、CPU、时区、API 地址、镜像标签/ID/摘要、评测程序实际版本，以及本次自动识别的 `cgroup_version` / `cgroup_mode`。这两个 cgroup 字段仅作记录，不用于强制选择；重新运行和健康检查都会按实际环境检测。secrets 目录只允许 root 访问，以只读目录挂载进容器；安装器不会把实际密码放进 docker run 的环境变量值或参数；官方启动脚本在容器内部读取密码并生成 restapi.secret。私有 HTTPS API 可使用 高级设置中的“提供私有 CA 公开证书文件”，证书会被复制到正式 secrets 目录。不要用 TLS 私钥作为 CA 文件，也不要关闭 TLS 验证。

改密操作：先在主站禁用该机器的评测实例、等待正在执行的提交完成，然后在 DOMjudge 更新 API 账号密码，编辑各宿主机 `api-password` 链接指向的正式文件，只写新密码并保持权限 600；重启该机全部受管理的容器。容器入口会重新生成内部 restapi.secret。共享 API 账号时，所有使用该账号的宿主机都要同步。

### 常用命令

~~~bash
systemctl status docker chrony
cat /sys/devices/system/cpu/online
docker ps --filter label=org.xcpc.owner=xcpc-fast-install
docker logs --tail 100 xcpc-judgehost-1
xcpc-check

# 在主站禁用并等待当前评测完成后维护：
docker stop xcpc-judgehost-1
# 维护完成：
docker start xcpc-judgehost-1
# 更新正式密码文件后：
docker restart xcpc-judgehost-1
~~~

在网页重新启用后，再执行 `xcpc-check`。本工具不会自动解除主站的禁用或评测限制。检查要求容器运行、重启策略/CPU/镜像符合配置，且主站记录 enabled、心跳不超过 120 秒。运行中不会仅为检查而重启容器；单纯看状态不要重跑安装。

日志保存在命名卷中，Docker stdout 日志限制为每文件 20 MiB、最多 5 个。DOMjudge 自身日志与评测数据卷仍需另行管理容量。不要执行 `docker volume prune`、`docker system prune --volumes` 清理办赛主机。

容器注册和心跳不等于所有语言通过判题：正式使用前对每种比赛语言提交 AC/WA/CE/TLE/MLE 样例，验证资源限制与封榜行为。

## 8. 失败和中断恢复

每次运行打印自己的日志路径。日志权限 600、目录 700；可在另一私有终端 tail -f 该日志。不要将包含配置的完整日志不加检查地公开。

安装锁使用 /run/xcpc-installer.lock 和系统 flock。文件存在不等于锁被占用；正常结束或进程退出后内核自动释放锁，不必删除文件。

### 下载或 APT 失败

修复网络、DNS、时间、软件源后，使用相同角色和身份参数重跑。成功的阶段会跳过，下载包必须通过 SHA-256 才会解包。高级设置中的“本机已有发行包目录”可以替代大体积发行包下载，不替代软件源。

### 数据库初始化失败

如果 domjudge 数据库存在，而“初始化空白 DOMjudge 数据库”阶段未标记完成，脚本会停止。不要执行 uninstall、load、DROP DATABASE 或手工伪造完成标记。

先保存数据库和安装日志，再用官方工具检查：

~~~bash
/opt/domjudge/domserver/bin/dj_setup_database -u root -s status
~~~

由运维人员确认初始化是否完整、是否已有比赛数据后，按官方维护流程修复；如果只是可以丢弃的首次安装测试机，可回到安装前快照，再从空白状态部署。

<a id="cgroup-v1v2-混合模式导致预检查失败"></a>

### cgroup 自动识别与排查

无需填写 cgroup 版本。单机、SSH 批量安装和 `xcpc-check` 使用同一套检测规则：

| 实际挂载 | 自动选择 | 检查条件 |
| --- | --- | --- |
| `/sys/fs/cgroup` 本身为 cgroup2 | v2 | 根层级可写、memory/cpuset 可用、内核 ≥5.19 |
| 根目录为 tmpfs，控制器分别为 cgroup | v1 | memory、cpuset、cpu、cpuacct 的挂载可写；内存和 swap 统计文件可读；cpuset CPU/NUMA 节点非空 |
| tmpfs 根目录、unified 子目录为 cgroup2，同时具有 v1 控制器 | v1 | 与上一行相同，不能把缺失的 v1 控制器用 v2 的同名控制器拼凑补齐 |
| 缺少有效层级或控制器 | 停止并说明原因 | 不强制猜测版本或在线切换 |

Docker 必须报告与检测结果一致的 cgroup 版本。容器启动后，健康检查还会读取容器内的层级，避免只检查宿主机而遗漏容器挂载问题。两种模式继续使用官方 `judgehost:latest`、host cgroup namespace 和同一套容器启动参数。

混合模式的正常提示示例：

~~~text
cgroup 自动识别：hybrid → 使用 v1。
PASS Docker 引擎：自动识别 cgroup v1（hybrid）
~~~

检查实际环境可执行以下只读命令：

~~~bash
findmnt -R /sys/fs/cgroup -o TARGET,FSTYPE,OPTIONS
cat /proc/self/cgroup
systemd-detect-virt
docker info --format 'cgroup={{.CgroupVersion}} driver={{.CgroupDriver}}'
cat /proc/cmdline
~~~

v1 若提示缺少 swap accounting，检查 `/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes` 和 `memory.memsw.max_usage_in_bytes`；若提示 cpu/cpuacct 挂载缺失，检查 `/sys/fs/cgroup/cpu`、`/sys/fs/cgroup/cpuacct` 是否指向对应合并挂载及 `cpuacct.usage` 是否可读。v2 若缺控制器或内核无法提供峰值内存统计，会给出对应提示，不会退到一个并不存在的可用 v1 环境。

1.3.0 及更早版本曾强制要求统一 v2，这是安装器的限制，不是 DOMjudge 9.0 不支持 v1。1.4.0 已取消这一限制；之前仅因 v1/混合模式预检查失败的机器，可以运行新版并选择继续 / 重试，无需清空安装状态。是否可部署仍取决于此次检测到的具体功能是否齐全。

自动识别与切换系统启动模式是两件事。现有 v1 满足条件时无需为本工具迁移到 v2。只有需要自行迁移系统模式时才涉及启动配置和重启，见 [Docker 说明](https://docs.docker.com/engine/containers/runmetrics/#changing-cgroup-version)。不要为了绕过检查在线卸载正在使用的控制器；安装器不会执行这项迁移。

预检查失败发生在 Docker 安装、镜像拉取和评测容器创建之前，只留下安装状态与日志。修复提示中的具体问题后重试；不要删除数据库、Docker 数据或整份安装状态。

### Docker 拉取或启动失败

先看私有安装日志及 `docker logs xcpc-judgehost-CPU编号`。网络失败可修复 Docker Hub/系统软件源连通性后再次选择继续 / 重试。只有拉取并读取实际版本成功后才记录镜像；后续使用已记录的镜像 ID，缺失时按记录摘要重新拉取。容器必须匹配原身份、CPU 和镜像记录才能继续使用。

内核/cgroup 条件不满足时改用满足要求的完整 VM/物理机，不把 Docker Desktop 或受限容器当作正式评测宿主机。容器反复重启常见原因是 API 密码、证书、CPU/沙箱用户或 cgroup 权限；先读日志，不删除评测数据卷。

只有原工具拥有、身份和镜像完全匹配的实例才会在重跑时被启动；有凭据更新时会记录待重启状态，成功验收后清除。已有比赛数据不因重试被删除。

### CDS / Live 没有比赛数据

检查 CDS ccs URL 是否指向具体比赛、账号是否只读可访问，Live contestId 是否一致。先执行 xcpc-check，再读服务日志。不要为解决网络故障授予集成账号 admin 权限。

Live 静态页面返回 200 不等于数据已到达。检查工具另外访问 /api/overlay/contestInfo，等待数据初始化；OBS 画面仍需实际查看。

### 时间同步失败

目标必须能访问有效 NTP。内网环境修改 /etc/chrony/chrony.conf 使用单位 NTP 后重启 chrony，等 chronyc tracking 正常再重跑。脚本不为“通过检查”关闭时间同步要求。

## 9. 备份和清理

快照适合首次部署回退。正式比赛还应准备数据库与文件的配套备份，覆盖 DOMjudge 的数据库、etc、submissions 等文件，以及 /etc/icpc-cds、/var/lib/icpc-cds、/etc/icpc-live、/var/lib/icpc-live 和 Nginx/systemd 配置。

数据库使用 mariadb-dump 的一致性备份选项；应用文件与数据库之间需要在暂停写入或明确维护窗口下取得一致的时间点。备份应保留属主、权限和符号链接，并进行恢复演练。不要将初始密码和 TLS 私钥备份到公开位置。

本版安装器不自动配置周期备份、清理策略或无人值守升级。下载/构建缓存不是比赛数据，但故障恢复期间应保留；安装完成并验证后，可按自己的运维策略清理缓存。不要删除 /var/lib/xcpc-installer、/etc/xcpc-judgehost、正在使用的 Docker 数据卷或 CDS 数据目录来“重置安装”。评测机备份至少覆盖私有配置、容器镜像标识和 judgings/logs 卷；采用维护窗口下的卷备份方案。

## 10. 验收清单

1. 三个管理入口可登录，根路径、证书及客户端 IP/域名正确。
2. xcpc-check 通过，CDS 比赛元数据、队伍、题目与 DOMjudge 一致，Live 收到比赛信息。
3. 每台评测机在 DOMjudge 中 enabled 且心跳正常。
4. 使用真实测试比赛验证各语言的 AC、WA、CE、TLE、MLE。
5. 演练重判、封榜、解榜，确认 Live/公开视图没有提前显示未公开结果。
6. 在实际 OBS 电脑上确认 overlay、图片、音频、视频和 WebSocket 工作。
7. 在维护窗口测试重启后恢复，并确认时间同步、备份和可用磁盘空间。

默认 JVM、FPM 和 MariaDB 参数是起始配置，不是容量保证。按参赛规模压测并调整，保持各评测机的硬件和语言环境尽量一致。
