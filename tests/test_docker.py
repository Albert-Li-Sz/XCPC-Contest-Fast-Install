import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import docker_judge as judge


class DockerJudging(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(api_url='http://192.0.2.10/api/', api_user='judgehost',
                        hostname='judge01', timezone='Asia/Shanghai', cpus=[1, 3])

    def test_isolated_cpu_identities(self):
        args1, args3 = judge.run_args(self.cfg, 1), judge.run_args(self.cfg, 3)
        self.assertEqual(args1[args1.index('--cpuset-cpus') + 1], '1')
        self.assertEqual(args3[args3.index('--cpuset-cpus') + 1], '3')
        self.assertNotEqual(judge.uid_for(1), judge.uid_for(3))
        self.assertNotEqual(judge.container_name(1), judge.container_name(3))

    def test_credentials_are_files_and_survive_atomic_replacement(self):
        self.cfg['password'] = 'example-secret-for-test'
        args = judge.run_args(self.cfg, 1)
        self.assertNotIn('example-secret-for-test', ' '.join(args))
        self.assertIn('JUDGEDAEMON_PASSWORD_FILE=/run/xcpc-secrets/password', args)
        mounts = [args[i + 1] for i, arg in enumerate(args) if arg == '--mount']
        self.assertIn('type=bind,src=/etc/xcpc-judgehost/secrets,dst=/run/xcpc-secrets,readonly', mounts)
        self.assertFalse(any('/secrets/password,' in mount for mount in mounts))

    def test_required_cgroup_permissions_and_durable_data(self):
        args = judge.run_args(self.cfg, 3)
        for option in ['--privileged', '--cgroupns=host', 'unless-stopped']:
            self.assertIn(option, args)
        self.assertIn('type=bind,src=/sys/fs/cgroup,dst=/sys/fs/cgroup', args)
        self.assertTrue(any('src=xcpc-judgehost-3-judgings,' in a for a in args))
        self.assertEqual(args[:3], ['docker', '--host', 'unix:///var/run/docker.sock'])

    def test_configuration_identity_changes_when_api_changes(self):
        original = judge.spec_hash(self.cfg, 1)
        self.cfg['api_url'] = 'https://judge.example.org/api/'
        self.assertNotEqual(original, judge.spec_hash(self.cfg, 1))

    def test_heartbeats_require_recent_finite_timestamp(self):
        with patch.object(judge.time, 'time', return_value=1000):
            for value in [None, True, False, 'NaN', 'inf', 0, 800, 1010, 'invalid']:
                with self.subTest(value=value):
                    self.assertFalse(judge.fresh_heartbeat(value))
            for value in [1000, 999.5, '990', 1004]:
                self.assertTrue(judge.fresh_heartbeat(value))

    def test_inspect_uses_local_engine_and_handles_missing_container(self):
        with patch.object(judge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            self.assertIsNone(judge.inspect('container', 'absent'))
        with patch.object(judge.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, json.dumps([{'Labels': None}]), '')) as run:
            self.assertIsNone(judge.inspect('volume', 'unmanaged')['Labels'])
            self.assertEqual(run.call_args.args[0][:3], judge.DOCKER)

    def test_image_recipe_pins_upstream_digest_and_source_checksum(self):
        source = (ROOT / 'src/docker/Dockerfile').read_text()
        import common
        self.assertIn('FROM ' + judge.BASE_IMAGE, source)
        self.assertIn(common.RELEASES['domjudge'][2], source)
        self.assertIn('9.0.1/', source)


if __name__ == '__main__':
    unittest.main()
