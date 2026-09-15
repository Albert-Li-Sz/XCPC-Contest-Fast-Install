import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import docker_judge

spec = importlib.util.spec_from_file_location('xcpc_preflight', ROOT / 'src/docker/preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

V2 = '31 24 0:28 / /sys/fs/cgroup rw,nosuid,nodev,noexec - cgroup2 cgroup rw,nsdelegate\n'
V1 = ('31 24 0:28 / /sys/fs/cgroup ro,nosuid,nodev - tmpfs tmpfs ro\n'
      '32 31 0:29 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n'
      '33 31 0:30 / /sys/fs/cgroup/cpuset rw - cgroup cgroup rw,cpuset\n'
      '35 31 0:33 / /sys/fs/cgroup/cpu,cpuacct rw - cgroup cgroup rw,cpu,cpuacct\n')
HYBRID = V1 + '34 31 0:31 / /sys/fs/cgroup/unified rw - cgroup2 cgroup rw,nsdelegate\n'


class CgroupPreflight(unittest.TestCase):
    def host(self, mounts, controllers, membership, container='none', distro='ubuntu', version='24.04', kernel='6.8.0-138-generic', overrides=None):
        files = {'/etc/os-release': f'ID={distro}\nVERSION_ID="{version}"\n',
                 '/proc/self/mountinfo': mounts, '/proc/self/cgroup': membership,
                 '/sys/devices/system/cpu/online': '0-151',
                 '/sys/fs/cgroup/cgroup.controllers': controllers,
                 '/sys/fs/cgroup/memory/memory.limit_in_bytes': '9223372036854771712',
                 '/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes': '9223372036854771712',
                 '/sys/fs/cgroup/memory/memory.memsw.max_usage_in_bytes': '12345678',
                 '/sys/fs/cgroup/cpuacct/cpuacct.usage': '123456789',
                 '/sys/fs/cgroup/cpuset/cpuset.cpus': '0-151',
                 '/sys/fs/cgroup/cpuset/cpuset.mems': '0-1'}
        files.update(overrides or {})
        def resolve(path, *args, **kwargs):
            if str(path) in {'/sys/fs/cgroup/cpu', '/sys/fs/cgroup/cpuacct'}:
                return Path('/sys/fs/cgroup/cpu,cpuacct')
            return path
        def read(path, *args, **kwargs):
            value = files.get(str(path))
            if value is None:
                raise FileNotFoundError(str(path))
            return value
        detection = subprocess.CompletedProcess([], 1 if container == 'none' else 0, container + '\n', '')
        with patch.object(Path, 'read_text', read), patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'is_dir', return_value=True), patch.object(Path, 'resolve', resolve), \
             patch.object(preflight.os, 'geteuid', return_value=0), \
             patch.object(preflight.platform, 'machine', return_value='x86_64'), \
             patch.object(preflight.platform, 'release', return_value=kernel), \
             patch.object(preflight.subprocess, 'run', return_value=detection):
            return preflight.check_host()

    def test_hybrid_host_is_diagnosed_without_false_container_error(self):
        report = self.host(HYBRID, None, '12:cpuset:/\n3:memory:/user.slice\n0::/user.slice\n')
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cgroup']['mode'], 'hybrid')
        self.assertEqual(report['cgroup']['version'], '1')
        self.assertEqual(report['container'], 'none')
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['online_cpus'], list(range(152)))

    def test_unified_host_root_membership_is_valid(self):
        report = self.host(V2, 'cpuset cpu io memory pids', '0::/\n')
        self.assertTrue(report['ok'])
        self.assertEqual(report['cgroup']['mode'], 'v2')

    def test_distro_names_do_not_reject_valid_judgehost(self):
        for distro, version in [('debian', '12'), ('ubuntu', '26.04'), ('fedora', '44'), ('custom', '')]:
            report = self.host(V2, 'memory cpuset cpu pids', '0::/\n', distro=distro, version=version)
            self.assertTrue(report['ok'], report['errors'])

    def test_legacy_mode_works_on_pre_519_kernel(self):
        report = self.host(V1, None, '1:memory:/\n2:cpuset:/\n', kernel='5.15.0-generic')
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['cgroup']['version'], '1')

    def test_v1_requires_swap_accounting_and_cpu_accounting(self):
        for path in ['memory/memory.memsw.limit_in_bytes', 'memory/memory.memsw.max_usage_in_bytes',
                     'cpuacct/cpuacct.usage', 'cpuset/cpuset.cpus', 'cpuset/cpuset.mems']:
            with self.subTest(path=path):
                report = self.host(HYBRID, None, '0::/\n', overrides={'/sys/fs/cgroup/' + path: None})
                self.assertFalse(report['ok'])
                self.assertIn(path, '\n'.join(report['errors']))

    def test_v1_readonly_controller_rejected_but_readonly_root_is_allowed(self):
        readonly = V1.replace('/memory rw - cgroup', '/memory ro - cgroup')
        self.assertFalse(self.host(readonly, None, '')['ok'])
        self.assertTrue(self.host(V1, None, '')['ok'])

    def test_v1_requires_cpu_and_cpuacct_mounts(self):
        mounts = '\n'.join(line for line in V1.splitlines() if 'cpu,cpuacct' not in line)
        report = self.host(mounts, None, '')
        self.assertFalse(report['ok'])
        self.assertIn('cpuacct', '\n'.join(report['errors']))

    def test_hybrid_does_not_combine_incomplete_v1_and_v2_controllers(self):
        mounts = '\n'.join(line for line in HYBRID.splitlines() if '/memory ' not in line)
        report = self.host(mounts, 'memory', '')
        self.assertFalse(report['ok'])
        self.assertEqual(report['cgroup']['version'], '1')
        self.assertIn('memory 控制器挂载', '\n'.join(report['errors']))

    def test_v2_peak_memory_still_requires_519(self):
        report = self.host(V2, 'memory cpuset', '0::/session', kernel='5.15.0-generic')
        self.assertFalse(report['ok'])
        self.assertIn('5.19', '\n'.join(report['errors']))

    def test_v2_root_wins_even_with_nested_v1_mounts(self):
        report = self.host(V2 + V1.splitlines()[-1], 'memory cpuset', '')
        self.assertTrue(report['ok'])
        self.assertEqual(report['cgroup']['version'], '2')

    def test_v2_readonly_root_is_rejected(self):
        readonly = V2.replace('/sys/fs/cgroup rw,', '/sys/fs/cgroup ro,')
        self.assertFalse(self.host(readonly, 'memory cpuset', '')['ok'])

    def test_v2_missing_required_controllers_remains_rejected(self):
        report = self.host(V2, 'cpu io pids', '0::/user.slice\n')
        self.assertFalse(report['ok'])
        self.assertIn('memory/cpuset', report['errors'][0])

    def test_explicit_container_detection_remains_rejected(self):
        report = self.host(V2, 'memory cpuset', '0::/\n', container='lxc')
        self.assertFalse(report['ok'])
        self.assertIn('lxc', report['errors'][0])

    def test_nested_v2_mount_does_not_count_as_unified_host(self):
        report, errors = preflight.cgroup_status(HYBRID.splitlines()[-1], 'memory cpuset')
        self.assertEqual(report['mode'], 'v2-nonstandard')
        self.assertTrue(errors)

    def test_failed_preflight_displays_reason_before_installation(self):
        report = self.host(HYBRID, None, '0::/user.slice\n',
                           overrides={'/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes': None})
        result = subprocess.CompletedProcess([], 1, json.dumps(report), '')
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(docker_judge.InstallError, 'swap accounting'):
                    docker_judge.install(rt, {}, None, None)
            self.assertEqual(json.loads(rt.log.read_text())['cgroup']['mode'], 'hybrid')
            rt.run.assert_not_called()
            rt.save.assert_not_called()

    def test_successful_report_continues_without_error(self):
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            result = subprocess.CompletedProcess([], 0, '{"ok":true,"errors":[],"cgroup":{"mode":"hybrid","version":"1"}}', '')
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                self.assertEqual(docker_judge.preflight(rt)['version'], '1')

    def test_broken_preflight_output_fails_with_log_location(self):
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            result = subprocess.CompletedProcess([], 1, '', 'interpreter error')
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(docker_judge.InstallError, '有效诊断'):
                    self.assertEqual(docker_judge.preflight(rt)['version'], '1')
            self.assertEqual(rt.log.read_text(), 'interpreter error')


if __name__ == '__main__':
    unittest.main()
