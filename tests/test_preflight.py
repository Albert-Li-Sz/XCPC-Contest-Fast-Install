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
      '33 31 0:30 / /sys/fs/cgroup/cpuset rw - cgroup cgroup rw,cpuset\n')
HYBRID = V1 + '34 31 0:31 / /sys/fs/cgroup/unified rw - cgroup2 cgroup rw,nsdelegate\n'


class CgroupPreflight(unittest.TestCase):
    def host(self, mounts, controllers, membership, container='none'):
        files = {'/etc/os-release': 'ID=ubuntu\nVERSION_ID="24.04"\n',
                 '/proc/self/mountinfo': mounts, '/proc/self/cgroup': membership,
                 '/sys/devices/system/cpu/online': '0-151',
                 '/sys/fs/cgroup/cgroup.controllers': controllers}
        def read(path, *args, **kwargs):
            value = files.get(str(path))
            if value is None:
                raise FileNotFoundError(str(path))
            return value
        detection = subprocess.CompletedProcess([], 1 if container == 'none' else 0, container + '\n', '')
        with patch.object(Path, 'read_text', read), patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'is_dir', return_value=True), \
             patch.object(preflight.os, 'geteuid', return_value=0), \
             patch.object(preflight.platform, 'machine', return_value='x86_64'), \
             patch.object(preflight.platform, 'release', return_value='6.8.0-138-generic'), \
             patch.object(preflight.subprocess, 'run', return_value=detection):
            return preflight.check_host()

    def test_hybrid_host_is_diagnosed_without_false_container_error(self):
        report = self.host(HYBRID, None, '12:cpuset:/\n3:memory:/user.slice\n0::/user.slice\n')
        self.assertFalse(report['ok'])
        self.assertEqual(report['cgroup']['mode'], 'hybrid')
        self.assertEqual(report['container'], 'none')
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('混合模式', report['errors'][0])
        self.assertEqual(report['online_cpus'], list(range(152)))

    def test_unified_host_root_membership_is_valid(self):
        report = self.host(V2, 'cpuset cpu io memory pids', '0::/\n')
        self.assertTrue(report['ok'])
        self.assertEqual(report['cgroup']['mode'], 'v2')

    def test_legacy_mode_remains_rejected(self):
        report = self.host(V1, None, '1:memory:/\n2:cpuset:/\n')
        self.assertFalse(report['ok'])
        self.assertEqual(report['cgroup']['mode'], 'v1')
        self.assertEqual(len(report['errors']), 1)

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
        report = self.host(HYBRID, None, '0::/user.slice\n')
        result = subprocess.CompletedProcess([], 1, json.dumps(report), '')
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(docker_judge.InstallError, '混合模式'):
                    docker_judge.install(rt, {}, None, None)
            self.assertEqual(json.loads(rt.log.read_text())['cgroup']['mode'], 'hybrid')
            rt.run.assert_not_called()
            rt.save.assert_not_called()

    def test_successful_report_continues_without_error(self):
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            result = subprocess.CompletedProcess([], 0, '{"ok":true,"errors":[]}', '')
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                docker_judge.preflight(rt)

    def test_broken_preflight_output_fails_with_log_location(self):
        with tempfile.TemporaryDirectory() as temp:
            rt = MagicMock()
            rt.log = Path(temp) / 'install.log'
            result = subprocess.CompletedProcess([], 1, '', 'interpreter error')
            with patch.object(docker_judge.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(docker_judge.InstallError, '有效诊断'):
                    docker_judge.preflight(rt)
            self.assertEqual(rt.log.read_text(), 'interpreter error')


if __name__ == '__main__':
    unittest.main()
