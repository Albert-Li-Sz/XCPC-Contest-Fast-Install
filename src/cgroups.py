"""Read-only cgroup selection matching the official judgehost's root-mount rule."""
from pathlib import Path
import re

BASE = '/sys/fs/cgroup'
V1_CONTROLLERS = ('memory', 'cpuset', 'cpu', 'cpuacct')


def read_text(path):
    return Path(path).read_text().strip()


def unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def cgroup_status(mountinfo, controllers):
    mounts = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(' - ')
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            continue
        target, kind = unescape_mount(fields[4]), filesystem[0]
        if (target == BASE or target.startswith(BASE + '/')) and kind in {'cgroup', 'cgroup2'}:
            mounts.append(dict(target=target, filesystem=kind,
                               options=fields[5].split(','), super_options=filesystem[2].split(',')))
    legacy = [m for m in mounts if m['filesystem'] == 'cgroup']
    unified = [m for m in mounts if m['filesystem'] == 'cgroup2']
    root = next((m for m in unified if m['target'] == BASE), None)
    mode = ('hybrid' if legacy and unified else 'v2' if root else 'v1' if legacy
            else 'v2-nonstandard' if unified else 'unavailable')
    # The upstream create_cgroups and runguard select v2 only at this root.
    version = '2' if root else '1' if legacy else None
    report = dict(mode=mode, version=version, mounts=mounts, controllers=[])
    errors = []
    if version == '2':
        report['controllers'] = (controllers or '').split()
        if 'rw' not in root['options'] or 'ro' in root['super_options']:
            errors.append('cgroup v2 根挂载只读，评测沙箱需要可写层级。')
        if controllers is None:
            errors.append('无法读取 cgroup v2 的 cgroup.controllers。')
        elif not {'memory', 'cpuset'}.issubset(report['controllers']):
            errors.append('cgroup v2 缺少 memory/cpuset 控制器；不会在线切换到 v1。')
    elif version == '1':
        for controller in V1_CONTROLLERS:
            path = Path(BASE) / controller
            target = str(path.resolve())
            mount = next((m for m in legacy if m['target'] == target
                          and controller in m['super_options']), None)
            if mount is None:
                errors.append(f'cgroup v1 缺少 {path} 对应的 {controller} 控制器挂载（可为标准软链接）。')
            elif 'rw' not in mount['options'] or 'ro' in mount['super_options']:
                errors.append(f'cgroup v1 的 {controller} 控制器只读。')
            else:
                report['controllers'].append(controller)
        for relative in ['memory/memory.limit_in_bytes', 'memory/memory.memsw.limit_in_bytes',
                         'memory/memory.memsw.max_usage_in_bytes', 'cpuacct/cpuacct.usage']:
            try:
                value = read_text(BASE + '/' + relative)
                if not value.isdecimal():
                    raise ValueError('not a counter')
            except (OSError, ValueError):
                errors.append(f'cgroup v1 缺少可读的 {relative}；memory.memsw.* 需要 swap accounting。')
        for relative in ['cpuset/cpuset.cpus', 'cpuset/cpuset.mems']:
            try:
                value = read_text(BASE + '/' + relative)
                if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
                    raise ValueError('empty or invalid cpuset')
            except (OSError, ValueError):
                errors.append(f'cgroup v1 的 {relative} 不可读或没有可用资源。')
    else:
        errors.append(f'无法自动选择 cgroup：当前模式为 {mode}，缺少官方镜像可用的根 v2 或标准 v1 层级。')
    return report, errors


def detect():
    try:
        controllers = read_text(BASE + '/cgroup.controllers')
    except OSError:
        controllers = None
    return cgroup_status(read_text('/proc/self/mountinfo'), controllers)


def kernel_errors(report, kernel):
    version = report.get('version')
    if version is None:
        return []
    minimum = (5, 19) if version == '2' else (3, 2)
    match = re.match(r'(\d+)\.(\d+)', kernel)
    if not match or tuple(map(int, match.groups())) < minimum:
        return [f'cgroup v{version} 需要 Linux 内核 {minimum[0]}.{minimum[1]} 或更新版本'
                + ('（v2 峰值内存统计要求）；不会在线改用 v1。' if version == '2' else '；还需满足 Docker Engine 自身要求。')]
    return []
