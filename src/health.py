#!/usr/bin/env python3
"""Read-only checks using current configuration, not stale initial passwords."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET

from common import STATE_DIR, InstallError, request, wait_for


def service(name):
    for operation in ["is-active", "is-enabled"]:
        result = subprocess.run(["systemctl", operation, "--quiet", name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode:
            raise InstallError(f"{name} 未运行或未设开机启动；查看 journalctl -u {name}")
    print(f"PASS 服务：{name}", flush=True)


def check():
    if os.geteuid() != 0:
        raise InstallError("检查配置需要 root。")
    cfg = json.loads((STATE_DIR / "state.json").read_text())["config"]
    if cfg["role"] == "judgehost":
        from docker_judge import check as check_docker
        service("docker")
        service("chrony")
        check_docker()
        clock_check()
        return
    import yaml
    for name in ["mariadb", "nginx", f"php{cfg['php']}-fpm", "chrony", "icpc-cds", "icpc-live"]:
        service(name)
    if request("http://127.0.0.1/api/info")["provider"]["version"] != "9.0.1":
        raise InstallError("DOMjudge API 版本不符。")
    if subprocess.run(["/opt/domjudge/domserver/bin/dj_setup_database", "-u", "root", "-s", "status"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode:
        raise InstallError("官方数据库状态检查失败。")
    print("PASS DOMjudge：根路径 API、版本、数据库", flush=True)
    ca = "/etc/icpc-cds/tls/cds.crt"
    accounts = yaml.safe_load(Path("/etc/icpc-cds/accounts.yaml").read_text())
    admin = next(a for a in accounts if a["type"] == "admin")
    contests = ET.parse("/etc/icpc-cds/cdsConfig.xml").getroot().findall("contest")
    if not contests:
        raise InstallError("CDS 尚未配置比赛。")
    for contest in contests:
        ccs = contest.find("ccs")
        if ccs is None:
            raise InstallError("当前检查工具要求 CDS 比赛具有 ccs 数据源。")
        cid = contest.get("id")
        target = "https://127.0.0.1:8443/api/contests/" + urllib.parse.quote(cid, safe="")
        origin = ccs.get("url").rstrip("/")
        def same_contest():
            source = request(origin, ccs.get("user"), ccs.get("password"))
            mirror = request(target, admin["username"], admin["password"], ca=ca)
            assert source["id"] == mirror["id"] == cid and source["name"] == mirror["name"]
        wait_for("CDS 比赛同步", same_contest, 180)
        # An administrative view must match the complete DOMjudge source, including frozen data.
        for kind in ["teams", "problems"]:
            def same_ids():
                source = request(origin + "/" + kind, ccs.get("user"), ccs.get("password"))
                mirror = request(target + "/" + kind, admin["username"], admin["password"], ca=ca)
                assert {x["id"] for x in source} == {x["id"] for x in mirror}
            wait_for("CDS " + kind, same_ids, 90)
        print(f"PASS CDS：{cid} 名称、队伍、题目与源站一致；TLS 校验通过", flush=True)
    settings = json.loads(Path("/etc/icpc-live/settings.json").read_text())
    creds = json.loads(Path("/etc/icpc-live/creds.json").read_text())
    feed = settings["feeds"][0]
    if feed["source"]["password"] != "$creds.cds_password":
        raise InstallError("Live 凭据引用已改变，请按自定义配置调整检查工具。")
    request(feed["source"]["url"].rstrip("/") + "/contests/" +
            urllib.parse.quote(feed["contestId"], safe=""),
            feed["source"]["login"], creds["cds_password"], ca=ca)
    wait_for("Live 管理页面", lambda: request("https://127.0.0.1:8444/admin", ca=ca, raw=True), 180)
    # This public endpoint waits for the data bus, unlike a static frontend page.
    live = wait_for("Live 赛事数据", lambda: request(
        "https://127.0.0.1:8444/api/overlay/contestInfo", ca=ca), 180)
    if not isinstance(live, dict):
        raise InstallError("Live 赛事信息格式不符。")
    print("PASS Live：CDS 公开凭据、HTTPS 页面与赛事数据接口", flush=True)
    clock_check()
    print("OK：服务与数据链路检查通过。真实判题、封榜及 OBS 画面请另做比赛演练。", flush=True)


def clock_check():
    if subprocess.run(["chronyc", "waitsync", "3", "0.5", "0", "5"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      timeout=25, check=False).returncode:
        raise InstallError("chrony 尚未同步或时钟偏差超过 0.5 秒。")
    print("PASS chrony：已同步，时钟偏差小于 0.5 秒", flush=True)


if __name__ == "__main__":
    try:
        check()
    except (InstallError, OSError, ValueError, KeyError, StopIteration,
            subprocess.TimeoutExpired, ET.ParseError) as error:
        print("FAIL：" + str(error), file=sys.stderr)
        raise SystemExit(1)
