"""Terminal prompts shared by single-host and batch installation wizards."""
import getpass
from pathlib import Path
import re
import warnings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common import InstallError, api_url, cpu_list, host


def ask(prompt, default=None, secret=False):
    suffix = f" [{default}]" if default not in (None, "") and not secret else ""
    try:
        # Separate streams work on non-seekable TTYs, including bash <(curl ...).
        with open("/dev/tty", "r") as reader, open("/dev/tty", "w") as writer:
            if secret:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    answer = getpass.getpass(prompt + "：", stream=writer)
            else:
                writer.write(prompt + suffix + "：")
                writer.flush()
                answer = reader.readline()
                if not answer:
                    raise InstallError("终端已关闭。")
                answer = answer.strip()
    except (OSError, EOFError, getpass.GetPassWarning):
        raise InstallError("无法读取交互终端；请在 SSH 终端内直接运行脚本，SSH 连接需要分配终端。") from None
    return answer if answer else default


def choose(title, options, default="0"):
    options = dict(options)
    while True:
        print("\n" + title, flush=True)
        for key, label in options.items():
            print(f"  {key}) {label}", flush=True)
        value = ask("请选择", default)
        if value in options:
            return value
        print("选项无效，请重新选择。", flush=True)


def field(prompt, default=None, validate=None, secret=False, optional=False):
    while True:
        value = ask(prompt, default, secret)
        if not value and optional:
            return None
        if not value:
            print("这一项不能为空，请重新填写。", flush=True)
            continue
        try:
            return validate(value) if validate else value
        except (InstallError, ValueError, OSError) as error:
            print("输入无效：" + ("请检查密码格式。" if secret else str(error)), flush=True)


def timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise InstallError("时区不存在，例如 Asia/Shanghai。") from None
    return value


def judge_name(value):
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise InstallError("使用小写字母开头的短主机名，只含字母、数字和连字符。")
    return value


def api_address(value):
    value = value.strip()
    if "://" not in value:
        value = "http://" + value
    from urllib.parse import urlsplit
    parts = urlsplit(value)
    host(parts.hostname or "")
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise InstallError("端口范围为 1–65535。")
    if parts.path in ("", "/"):
        value = value.rstrip("/") + "/api/"
    elif parts.path.endswith("/api"):
        value += "/"
    return api_url(value)


def token(value):
    if not re.fullmatch(r"\S+", value):
        raise InstallError("不能包含空格或换行。")
    return value


def username(value):
    token(value)
    if ":" in value:
        raise InstallError("API 用户名不能包含冒号。")
    return value


def existing_file(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise InstallError("文件不存在。")
    return str(path)


def directory(value):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise InstallError("目录不存在。")
    return str(path)


def integer(value, low, high):
    if not value.isdigit() or not low <= int(value) <= high:
        raise InstallError(f"请输入 {low}–{high} 范围内的整数。")
    return int(value)


def cpus(value, online=None):
    values = cpu_list(value.replace(" ", ""))
    if online is not None and not set(values).issubset(online):
        raise InstallError("所选 CPU 不在上方显示的在线列表中。")
    return ",".join(map(str, values))


def online_cpus():
    source = Path("/sys/devices/system/cpu/online")
    if not source.exists():
        return None
    result = set()
    for part in source.read_text().strip().split(","):
        bounds = list(map(int, part.split("-")))
        result.update(range(bounds[0], bounds[-1] + 1))
    return result
