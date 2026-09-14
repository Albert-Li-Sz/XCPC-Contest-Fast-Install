# 办赛配置入口

访问：DOMjudge http://@@HOST@@/，CDS https://@@HOST@@:8443/，Live https://@@HOST@@:8444/admin。

- domjudge/php-fpm.conf：并发和上传限制；修改后先测试 FPM 配置，再 reload 对应 PHP-FPM。
- cds/cdsConfig.xml：比赛 ID、DOMjudge 数据源和同步账号；修改后重启 icpc-cds。
- cds/accounts.yaml：CDS 账号；live-reader 改密时同步 live/creds.json。
- live/settings.json：CDS 比赛数据源、contestId、素材 URL 映射；修改后重启 icpc-live。
- live/creds.json：Live 连接 CDS 的密码。
- deployment.md：完整运维说明。
- initial-credentials.txt：初始管理员密码，root 专用；改密后不会自动更新。

比赛、题目、队伍、网站账号、评测机启用状态在 DOMjudge 网页操作；Live 视觉配置、预设、素材和账号确认在 Live 后台操作。它们不另建文件快捷入口。

软链接直接指向生效配置，修改前先备份目标文件。检查命令：xcpc-check。

本目录与密码记录只允许 root 读取。分享部署说明时不要一起分享密码文件。
