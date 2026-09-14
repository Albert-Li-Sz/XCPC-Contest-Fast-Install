"""DOMserver + CDS + Live installation; no judgehost or kernel tuning."""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import secrets
import shutil
import socket
from urllib.parse import quote
import xml.etree.ElementTree as ET

from common import (CACHE, STATE_DIR, INSTALL_ROOT, InstallError, mkdir, write, link,
                    render, extract_tar, extract_zip, request, wait_for)

DOM = Path("/opt/domjudge/domserver")
CDS = Path("/etc/icpc-cds")
LIVE = Path("/etc/icpc-live")
JAVA = Path("/usr/lib/jvm/java-21-openjdk-amd64")


def new_user(rt, name, home="/nonexistent"):
    try:
        pwd.getpwnam(name)
    except KeyError:
        rt.run(["useradd", "--system", "--user-group", "--home-dir", home,
                "--no-create-home", "--shell", "/usr/sbin/nologin", name])


def database_exists(rt):
    return rt.run(["mariadb", "--batch", "--skip-column-names", "-e",
                   "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='domjudge'"],
                  capture=True) == "domjudge"


def fresh_server_check():
    if Path('/var/lib/mysql/mysql').exists() or Path('/var/lib/mysql/domjudge').exists():
        raise InstallError('发现已有数据库实例；需要空白主机，不接管已有 MariaDB/MySQL。')
    for path in ["/opt/domjudge", "/etc/icpc-cds", "/opt/icpctools", "/etc/icpc-live",
                 "/opt/icpc-live", "/var/lib/icpc-cds", "/var/lib/icpc-live", "/root/contest"]:
        if Path(path).exists():
            raise InstallError(f"需要独立空白主机；发现已有目录 {path}，不会接管或覆盖。")
    enabled = Path("/etc/nginx/sites-enabled")
    if enabled.exists() and any(p.name != "default" for p in enabled.iterdir()):
        raise InstallError("存在其他 Nginx 站点；请改用独立空白主机。")
    if list(Path("/etc/nginx/conf.d").glob("*.conf")):
        raise InstallError("已有 Nginx conf.d 配置；请改用独立空白主机。")
    for port in [80, 8081, 8443, 8444]:
        probe = socket.socket()
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            raise InstallError(f"端口 {port} 已占用；请在独立空白主机上安装。") from None
        finally:
            probe.close()


def install(rt, cfg):
    private_path = STATE_DIR / "secrets.json"
    if private_path.exists():
        private = json.loads(private_path.read_text())
    else:
        private = {name: secrets.token_urlsafe(24) for name in
                   ["cds_source", "cds_admin", "pres_admin", "staff", "presentation",
                    "balloon", "live_reader", "live_admin", "keystore"]}
        write(private_path, json.dumps(private, indent=2) + "\n", 0o600)

    def packages():
        rt.run(["apt-get", "update"])
        rt.run(["apt-get", "-o", "Dpkg::Lock::Timeout=300", "install", "-y",
                "build-essential", "pkg-config", "libcgroup-dev", "acl", "zip", "unzip", "pv",
                "curl", "ca-certificates", "mariadb-server", "nginx", "apache2-utils", "chrony",
                "php-cli", "php-fpm", "php-gd", "php-intl", "php-mbstring", "php-mysql",
                "php-curl", "php-xml", "php-zip", "php-bcmath", "composer", "python3-yaml", "python3-requests",
                "openjdk-21-jre-headless", "openssl", "iproute2"])
        # Distros choose PHP 8.3 (Ubuntu 24.04) or 8.4 (Debian 13).
        cfg["php"] = rt.run(["php", "-r", 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;'], capture=True)
        rt.save()
        rt.run(["timedatectl", "set-timezone", cfg["timezone"]])
        rt.run(["systemctl", "enable", "--now", "chrony", "mariadb"])
        rt.run(["chronyc", "waitsync", "12", "0.5", "0", "5"], timeout=75)

    rt.step("基础软件与时间同步", packages)
    # A crash before packages finished can leave PHP discovery unsaved.
    if "php" not in cfg:
        cfg["php"] = rt.run(["php", "-r", 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;'], capture=True)
        rt.save()

    def build_domserver():
        new_user(rt, "domjudge")
        archive = rt.artifact("domjudge")
        source = CACHE / "domjudge-9.0.1"
        if not (source / "configure").exists():
            extract_tar(archive, CACHE)
        rt.run(["./configure", "--prefix=/opt/domjudge", "--with-domjudge-user=domjudge",
                "--with-webserver-group=www-data", "--with-baseurl=http://" + cfg["host"] + "/"],
               cwd=source)
        rt.run(["make", "-j" + str(min(os.cpu_count() or 1, 4)), "domserver"], cwd=source)
        rt.run(["make", "install-domserver"], cwd=source)

    rt.step("构建 DOMjudge 9.0.1 DOMserver", build_domserver)

    def database():
        # dj_setup_database install must never be retried against an existing database.
        if database_exists(rt):
            raise InstallError("domjudge 数据库已存在而初始化标记缺失；停止，避免重置比赛数据。见故障恢复说明。")
        write("/etc/mysql/mariadb.conf.d/60-domjudge.cnf",
              render("mariadb.cnf", DB_BUFFER=cfg["db_buffer"]))
        rt.run(["systemctl", "restart", "mariadb"])
        rt.run([DOM / "bin/dj_setup_database", "genpass"])
        rt.run([DOM / "bin/dj_setup_database", "-u", "root", "-s", "install"])
        rt.run([DOM / "bin/dj_setup_database", "-u", "root", "-s", "status"])

    rt.step("初始化空白 DOMjudge 数据库", database)

    def web():
        for template, target in [("domjudge-nginx.conf", "nginx-conf"),
                                 ("domjudge-nginx-inner.conf", "nginx-conf-inner"),
                                 ("domjudge-fpm.conf", "domjudge-fpm.conf")]:
            write(DOM / "etc" / target, render(template, HOST=cfg["host"],
                  TIMEZONE=cfg["timezone"], PHP_CHILDREN=cfg["php_children"]), 0o644)
        default = Path("/etc/nginx/sites-enabled/default")
        if default.is_symlink() and default.resolve() == Path("/etc/nginx/sites-available/default"):
            default.unlink()
        elif default.exists():
            raise InstallError("default 不是发行版的默认站点链接，未删除。")
        link(DOM / "etc/nginx-conf", "/etc/nginx/sites-enabled/domjudge")
        link(DOM / "etc/domjudge-fpm.conf", f"/etc/php/{cfg['php']}/fpm/pool.d/domjudge.conf")
        for sapi in ["cli", "fpm"]:
            write(f"/etc/php/{cfg['php']}/{sapi}/conf.d/99-domjudge.ini",
                  "date.timezone=" + cfg["timezone"] + "\n", 0o644)
        rt.run([DOM / "bin/fix_permissions"])
        rt.run([f"php-fpm{cfg['php']}", "-t"])
        rt.run(["nginx", "-t"])
        rt.run(["systemctl", "enable", "--now", f"php{cfg['php']}-fpm", "nginx"])
        rt.run(["systemctl", "reload", f"php{cfg['php']}-fpm", "nginx"])
        def ready():
            assert request("http://127.0.0.1/api/info")["provider"]["version"] == "9.0.1"
        wait_for("DOMjudge API", ready)

    rt.step("DOMjudge 根路径与 PHP-FPM", web)

    def integration_user():
        admin = (DOM / "etc/initial_admin_password.secret").read_text().strip()
        users = request("http://127.0.0.1/api/users", "admin", admin)
        existing = [u for u in users if u.get("username") == "cds-reader"]
        if not existing:
            request("http://127.0.0.1/api/users", "admin", admin, data={
                "username": "cds-reader", "name": "CDS data source",
                "password": private["cds_source"], "enabled": True,
                "roles": ["api_reader", "api_source_reader"],
            })
        identity = request("http://127.0.0.1/api/user", "cds-reader", private["cds_source"])
        if set(identity.get("roles", [])) != {"api_reader", "api_source_reader"}:
            raise InstallError("已有 cds-reader 凭据或角色不符；不会重置已有账号。")
        contests = request("http://127.0.0.1/api/contests", "admin", admin)
        requested = cfg.get("contest_id")
        if requested:
            if not any(c["id"] == requested for c in contests):
                raise InstallError("指定比赛 ID 不存在；先在 DOMjudge 网页创建比赛。")
        elif contests:
            cfg["contest_id"] = str(contests[0]["id"])
        else:
            raise InstallError("DOMjudge 未返回比赛；请先在网页创建比赛再重试。")
        rt.save()

    rt.step("建立只读同步账号并选择示例比赛", integration_user)

    def cds():
        import yaml
        new_user(rt, "cds", "/var/lib/icpc-cds")
        release = Path("/opt/icpctools/cds-2.6.1331")
        mkdir("/opt/icpctools", 0o755)
        mkdir(release, group="cds")
        extract_zip(rt.artifact("cds"), release)
        cds_gid = grp.getgrnam("cds").gr_gid
        for path in [release, *release.rglob("*")]:
            os.chown(path, 0, cds_gid)
            path.chmod(0o750 if path.is_dir() else 0o640 | (path.stat().st_mode & 0o110))
        (release / "wlp/bin/server").chmod(0o750)
        link(release, "/opt/icpctools/cds")
        mkdir(CDS, group="cds")
        mkdir(CDS / "tls", group="cds")
        for directory in ["", "usr", "usr/servers", "usr/servers/cds", "usr/servers/cds/apps",
                          "contests", "contests/current", "tmp", "output"]:
            mkdir(Path("/var/lib/icpc-cds") / directory, user="cds", group="cds")
        accounts = [{"username": user, "password": private[key], "type": role}
            for user, key, role in [
                ("admin", "cds_admin", "admin"), ("presAdmin", "pres_admin", "presAdmin"),
                ("staff", "staff", "staff"), ("presentation", "presentation", "public"),
                ("balloon", "balloon", "balloon"), ("live-reader", "live_reader", "public")]]
        write(CDS / "accounts.yaml", yaml.safe_dump(accounts, sort_keys=False), group="cds")
        doc = ET.Element("cds", name="DOMjudge CDS")
        contest = ET.SubElement(doc, "contest", id=cfg["contest_id"],
                                path="/var/lib/icpc-cds/contests/current", recordReactions="false")
        ET.SubElement(contest, "ccs", url="http://127.0.0.1/api/contests/" +
                      quote(cfg["contest_id"], safe=""),
                      user="cds-reader", password=private["cds_source"])
        domain = ET.SubElement(doc, "domain")
        ET.SubElement(domain, "user", name="presAdmin")
        ET.SubElement(domain, "user", name="presentation")
        ET.indent(doc)
        write(CDS / "cdsConfig.xml", ET.tostring(doc, encoding="unicode") + "\n", group="cds")
        san_host = ("IP:" if cfg["host"].replace(".", "").isdigit() else "DNS:") + cfg["host"]
        if not (CDS / "tls/cds.key").exists():
            rt.run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
                    "-days", "825", "-keyout", CDS / "tls/cds.key", "-out", CDS / "tls/cds.crt",
                    "-subj", "/CN=" + cfg["host"] + "/O=XCPC",
                    "-addext", f"subjectAltName={san_host},IP:127.0.0.1,DNS:localhost"])
        if not (CDS / "tls/cds.crt").exists():
            raise InstallError("TLS 密钥已存在但证书不完整；请检查 TLS 目录后恢复。")
        rt.run(["openssl", "pkcs12", "-export", "-name", "cds", "-inkey", CDS / "tls/cds.key",
                "-in", CDS / "tls/cds.crt", "-out", CDS / "tls/cds.p12", "-passout", "stdin"],
               input_text=private["keystore"] + "\n")
        os.chmod(CDS / "tls/cds.key", 0o600)
        for filename in ["cds.crt", "cds.p12"]:
            os.chown(CDS / "tls" / filename, 0, cds_gid)
            os.chmod(CDS / "tls" / filename, 0o640)
        write(CDS / "server.env", f"JAVA_HOME={JAVA}\nCDS_KEYSTORE_PASSWORD={private['keystore']}\n"
              f"ICPC_TOOLS_IP={cfg['host']}\n", group="cds")
        write(CDS / "server.xml", render("cds-server.xml"), group="cds")
        write(CDS / "jvm.options", render("cds-jvm.options", TIMEZONE=cfg["timezone"]), group="cds")
        service_root = Path("/var/lib/icpc-cds/usr/servers/cds")
        for filename in ["server.xml", "server.env", "jvm.options"]:
            link(CDS / filename, service_root / filename)
        link("/opt/icpctools/cds/wlp/usr/servers/cds/apps/CDS.war", service_root / "apps/CDS.war")
        write("/etc/systemd/system/icpc-cds.service", render("icpc-cds.service"), 0o644)
        rt.run(["systemctl", "daemon-reload"])
        rt.run(["systemctl", "enable", "icpc-cds"])
        rt.run(["systemctl", "restart", "icpc-cds"])
        def ready():
            result = request("https://127.0.0.1:8443/api/contests/" + quote(cfg["contest_id"], safe=""),
                             "admin", private["cds_admin"], ca=CDS / "tls/cds.crt")
            assert result["id"] == cfg["contest_id"]
        wait_for("CDS 比赛数据", ready, 300)

    rt.step("安装 CDS 并连通 DOMjudge", cds)

    def live():
        new_user(rt, "icpclive", "/var/lib/icpc-live")
        mkdir("/opt/icpc-live", 0o755)
        mkdir("/opt/icpc-live/releases", 0o755)
        release = mkdir("/opt/icpc-live/releases/3.5.0", group="icpclive")
        shutil.copyfile(rt.artifact("live"), release / "live-v3.jar")
        os.chown(release / "live-v3.jar", 0, grp.getgrnam("icpclive").gr_gid)
        (release / "live-v3.jar").chmod(0o640)
        link(release, "/opt/icpc-live/current")
        mkdir(LIVE, group="icpclive")
        for directory in ["", "config", "media", "presets", "tmp"]:
            mkdir(Path("/var/lib/icpc-live") / directory, user="icpclive", group="icpclive")
        settings = {"type": "clics", "feeds": [{
            "source": {"url": "https://127.0.0.1:8443/api", "login": "live-reader",
                       "password": "$creds.cds_password"},
            "contestId": cfg["contest_id"], "feedVersion": "2023_06",
            "urlPrefixMapping": {"contests/": f"https://{cfg['host']}:8444/cds/contests/"}
        }], "network": {"allowUnsecureConnections": False}}
        write(LIVE / "settings.json", json.dumps(settings, indent=2) + "\n", group="icpclive")
        write(LIVE / "creds.json", json.dumps({"cds_password": private["live_reader"]}) + "\n",
              group="icpclive")
        link(LIVE / "settings.json", "/var/lib/icpc-live/config/settings.json")
        # Preserve files that Live may already have changed during an interrupted install.
        for filename, text in [("advanced.json", "[]\n"), ("visual-config.json", "{}\n")]:
            path = Path("/var/lib/icpc-live/config") / filename
            if not path.exists():
                write(path, text, user="icpclive", group="icpclive")
        users_file = Path("/var/lib/icpc-live/users.json")
        if not users_file.exists():
            password_hash = base64.b64encode(hashlib.sha256(
                (":icpc:admin:live:" + private["live_admin"]).encode()).digest()).decode()
            write(users_file, json.dumps([{"name": "admin", "pass": password_hash,
                                          "confirmed": True}]) + "\n", 0o600,
                  user="icpclive", group="icpclive")
        shutil.copyfile("/etc/ssl/certs/java/cacerts", LIVE / "truststore.jks")
        rt.run([JAVA / "bin/keytool", "-importcert", "-noprompt", "-alias", "xcpc-local-cds",
                "-file", CDS / "tls/cds.crt", "-keystore", LIVE / "truststore.jks",
                "-storepass", "changeit"])
        os.chown(LIVE / "truststore.jks", 0, grp.getgrnam("icpclive").gr_gid)
        (LIVE / "truststore.jks").chmod(0o640)
        write("/etc/systemd/system/icpc-live.service",
              render("icpc-live.service", TIMEZONE=cfg["timezone"]), 0o644)
        write("/etc/nginx/sites-available/icpc-live",
              render("live-nginx.conf", HOST=cfg["host"]), 0o644)
        link("/etc/nginx/sites-available/icpc-live", "/etc/nginx/sites-enabled/icpc-live")
        rt.run(["nginx", "-t"])
        rt.run(["systemctl", "daemon-reload"])
        rt.run(["systemctl", "enable", "icpc-live"])
        rt.run(["systemctl", "restart", "icpc-live"])
        rt.run(["systemctl", "reload", "nginx"])
        wait_for("Live 页面", lambda: request("http://127.0.0.1:8081/admin", raw=True), 240)

    rt.step("安装 ICPC Live 并连通 CDS", live)

    def shortcuts():
        mkdir("/root/contest", 0o700)
        for app in ["domjudge", "cds", "live"]:
            mkdir("/root/contest/" + app, 0o700)
        links = {
            "domjudge/php-fpm.conf": DOM / "etc/domjudge-fpm.conf",
            "cds/cdsConfig.xml": CDS / "cdsConfig.xml",
            "cds/accounts.yaml": CDS / "accounts.yaml",
            "live/settings.json": LIVE / "settings.json",
            "live/creds.json": LIVE / "creds.json",
        }
        for relative, target in links.items():
            link(target, Path("/root/contest") / relative)
        write("/root/contest/README.md", render("contest-readme.md", HOST=cfg["host"]), 0o600)
        link(INSTALL_ROOT / "docs/OPERATIONS.md", "/root/contest/deployment.md")
        # Credentials are private, separate from the shareable operation guide.
        admin = (DOM / "etc/initial_admin_password.secret").read_text().strip()
        write("/root/contest/initial-credentials.txt",
              "仅代表初始密码，网页改密后本记录不会更新。\n"
              f"DOMjudge http://{cfg['host']}/  admin  {admin}\n"
              f"CDS https://{cfg['host']}:8443/  admin  {private['cds_admin']}\n"
              f"Live https://{cfg['host']}:8444/admin  admin  {private['live_admin']}\n"
              "Judgehost API 凭据见 /opt/domjudge/domserver/etc/restapi.secret\n"
              "CDS 其他账号见 /etc/icpc-cds/accounts.yaml\n", 0o600)

    rt.step("整理配置入口与私有密码记录", shortcuts)
