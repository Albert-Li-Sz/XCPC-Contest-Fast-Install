"""Small, auditable primitives for fresh Linux installations."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import grp
import re
import shutil
import ssl
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

VERSION = "1.4.0"
STATE_DIR = Path("/var/lib/xcpc-installer")
CACHE = Path("/var/cache/xcpc-installer")
INSTALL_ROOT = Path("/opt/xcpc-installer")
RELEASES = {
    "domjudge": ("domjudge-9.0.1.tar.gz",
        "https://www.domjudge.org/releases/domjudge-9.0.1.tar.gz",
        "3d46b31c296581833ac9f8e9b249d929fc28710e834b1cec68723791a2dceb97"),
    "cds": ("wlp.CDS-2.6.1331.zip",
        "https://github.com/icpctools/icpctools/releases/download/v2.6.1331/wlp.CDS-2.6.1331.zip",
        "cab85a8d29e997f196f8bf2563e58a96b17251d4bc57d9b44c6aa217205245db"),
    "live": ("live-v3-3.5.0.jar",
        "https://github.com/icpc/live-v3/releases/download/v3.5.0/live-v3-3.5.0.jar",
        "b5327f725b05da2d207423e971c3c6a3223e18175db3ab4e08203a7be793851c"),
}


class InstallError(Exception):
    pass


SERVER_SYSTEMS = {("debian", "12"), ("debian", "13"), ("ubuntu", "24.04"), ("ubuntu", "26.04")}


def system_release():
    release = {"ID": "linux", "VERSION_ID": "unknown"}
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
    except FileNotFoundError:
        return release
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    return release


def server_runtime(os_id, version):
    if (os_id, version) not in SERVER_SYSTEMS:
        raise InstallError("主站支持 Debian 12/13、Ubuntu 24.04/26.04。评测机不限制发行版版本。")
    java = 17 if (os_id, version) == ("debian", "12") else 21
    return {"java_major": java, "java_home": f"/usr/lib/jvm/java-{java}-openjdk-amd64"}


def host(value):
    """Only a single IPv4 address or DNS hostname, never a URL/config fragment."""
    value = value.strip()
    try:
        address = ipaddress.ip_address(value)
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise InstallError("请使用有效 IPv4 或 DNS 名称；此版不配置 IPv6 入口。")
        return str(address)
    except ValueError:
        if re.fullmatch(r"[0-9.]+", value):
            raise InstallError("IPv4 地址无效。") from None
    if len(value) > 253 or not value or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    ):
        raise InstallError("请输入 IPv4 或 DNS 名称，不含协议、端口、路径或空白。")
    return value.lower()


def api_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or re.search(r"\s", value)):
        raise InstallError("API URL 格式无效。")
    if not parsed.path.endswith("/api/"):
        raise InstallError("API URL 必须以 /api/ 结尾。")
    return value


def cpu_list(value):
    if not re.fullmatch(r"\d+(?:,\d+)*", value):
        raise InstallError("CPU 填逗号分隔的整数，例如 1 或 1,3。")
    cpus = [int(x) for x in value.split(",")]
    if len(set(cpus)) != len(cpus):
        raise InstallError("CPU 编号不能重复。")
    return cpus


def mkdir(path, mode=0o750, user="root", group="root"):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    os.chown(path, pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid)
    return path


def write(path, text, mode=0o640, user="root", group="root"):
    path = Path(path)
    if path.is_symlink():
        raise InstallError(f"拒绝覆盖符号链接：{path}")
    temp = path.with_name(path.name + ".xcpc-new")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    os.chown(temp, pwd.getpwnam(user).pw_uid, grp.getgrnam(group).gr_gid)
    temp.chmod(mode)
    os.replace(temp, path)


def link(target, destination):
    destination = Path(destination)
    if destination.is_symlink():
        if os.readlink(destination) == str(target):
            return
        raise InstallError(f"已有其他软链接，未覆盖：{destination}")
    if destination.exists():
        raise InstallError(f"已有普通文件，未覆盖：{destination}")
    destination.symlink_to(target)


def render(name, **values):
    text = (Path(__file__).parent / "templates" / name).read_text()
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", str(value))
    if re.search(r"@@[A-Z_]+@@", text):
        raise InstallError(f"模板参数未填全：{name}")
    return text


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, user=None, password=None, data=None, ca=None, raw=False, method=None):
    headers = {"Accept": "application/json"}
    if user is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    context = ssl.create_default_context(cafile=str(ca) if ca else None)
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=20) as response:
            return response.read() if raw else json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise InstallError(f"HTTP 检查失败（{code}）：{urllib.parse.urlsplit(url).path}") from None
    except (OSError, ValueError):
        raise InstallError(f"无法读取接口：{urllib.parse.urlsplit(url).path}，请检查网络、TLS 和服务日志。") from None


class Runtime:
    def __init__(self, state, log, artifact_dir=None):
        self.state, self.log = state, log
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    def save(self):
        write(STATE_DIR / "state.json", json.dumps(self.state, indent=2) + "\n", 0o600)

    def run(self, args, *, input_text=None, env=None, cwd=None, timeout=None, capture=False):
        with self.log.open("a") as out:
            process = subprocess.run(
                list(map(str, args)), input=input_text, text=True, cwd=cwd,
                stdout=subprocess.PIPE if capture else out, stderr=subprocess.STDOUT,
                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C", **(env or {})},
                timeout=timeout, check=False,
            )
            # Private output only; never log supplied argv, stdin or environment.
            if capture:
                out.write(process.stdout)
        if process.returncode:
            raise InstallError(f"{Path(str(args[0])).name} 执行失败（{process.returncode}）；查看私有日志 {self.log}")
        return process.stdout.strip() if capture else None

    def step(self, name, function):
        if name in self.state["done"]:
            print(f"已完成，跳过：{name}", flush=True)
            return
        print(f"正在执行：{name}", flush=True)
        function()
        self.state["done"].append(name)
        self.save()
        print(f"完成：{name}", flush=True)

    def artifact(self, component):
        filename, url, expected = RELEASES[component]
        destination = CACHE / filename
        if destination.exists() and digest(destination) == expected:
            return destination
        if self.artifact_dir:
            source = self.artifact_dir / filename
            if not source.is_file() or digest(source) != expected:
                raise InstallError(f"本地发行包缺失或 SHA-256 不符：{filename}")
            shutil.copyfile(source, destination)
        else:
            temporary = destination.with_suffix(destination.suffix + ".part")
            self.run(["curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2",
                      "--retry", "3", "--connect-timeout", "20", "--max-time", "3600",
                      "--output", temporary, url])
            if digest(temporary) != expected:
                temporary.unlink()
                raise InstallError(f"发行包 SHA-256 不符：{filename}")
            os.replace(temporary, destination)
        destination.chmod(0o600)
        return destination


def extract_tar(archive, destination):
    with tarfile.open(archive) as bundle:
        if hasattr(tarfile, "data_filter"):
            try:
                bundle.extractall(destination, filter="data")
            except tarfile.FilterError as error:
                raise InstallError("TAR 包含不安全路径或文件类型。") from error
        else:
            # Debian 12's Python 3.11.2 lacks extraction filters. Extract only
            # regular files/directories, then create validated relative symlinks.
            extract_tar_legacy(bundle, destination)


def extract_tar_legacy(bundle, destination):
    base = Path(destination).resolve()
    base.mkdir(parents=True, exist_ok=True)
    members = bundle.getmembers()
    links = {Path(m.name) for m in members if m.issym()}
    seen = set()
    for member in members:
        name = Path(member.name)
        if name.is_absolute() or ".." in name.parts or name in seen:
            raise InstallError("TAR 包含越界或重复路径。")
        seen.add(name)
        if any(parent in links for parent in name.parents):
            raise InstallError("TAR 不允许通过符号链接目录写入文件。")
        if not (member.isfile() or member.isdir() or member.issym()):
            raise InstallError("TAR 包含不支持的特殊文件或硬链接。")
        target = base / name
        if not target.resolve().is_relative_to(base):
            raise InstallError("TAR 目标路径越界。")
        if member.issym():
            linkname = Path(member.linkname)
            if linkname.is_absolute() or not (target.parent / linkname).resolve().is_relative_to(base):
                raise InstallError("TAR 符号链接目标越界。")
    for member in members:
        if member.issym():
            continue
        target = base / member.name
        if target.is_symlink():
            raise InstallError("TAR 不覆盖已有符号链接。")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o755)
    for member in members:
        if not member.issym():
            continue
        target = base / member.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and os.readlink(target) == member.linkname:
            continue
        if target.exists() or target.is_symlink():
            raise InstallError("TAR 不覆盖已有链接目标。")
        target.symlink_to(member.linkname)
    # Preserve traversal permissions even under the entry's private umask 077.
    for member in reversed(members):
        if member.isdir():
            (base / member.name).chmod((member.mode & 0o755) | 0o700)
    # Recheck forward references once the complete symlink graph exists.
    for name in links:
        try:
            safe = (base / name).resolve().is_relative_to(base)
            # Python 3.13+ non-strict resolve can retain a symlink loop.
            # stat detects ELOOP while dangling in-tree links remain allowed.
            try:
                (base / name).stat()
            except FileNotFoundError:
                pass
        except (OSError, RuntimeError):
            safe = False
        if not safe:
            (base / name).unlink()
            raise InstallError("TAR 符号链接链越界或形成循环。")


def extract_zip(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination):
                raise InstallError("ZIP 包含越界路径。")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise InstallError("ZIP 包含意外符号链接。")
            bundle.extract(entry, destination)
            if not entry.is_dir():
                target.chmod(0o640 | ((entry.external_attr >> 16) & 0o110))


def wait_for(label, function, seconds=180):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return function()
        except (InstallError, KeyError, AssertionError):
            if time.monotonic() >= deadline:
                raise InstallError(f"等待超时：{label}。检查服务日志后重新运行检查。") from None
            time.sleep(3)
