import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

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

    def test_health_rejects_cgroup_v1_before_reading_credentials(self):
        with patch.object(judge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '1', '')):
            with self.assertRaisesRegex(judge.InstallError, 'cgroup v2'):
                judge.check()

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

    def test_official_base_url_and_password_file(self):
        for url, expected in [('http://192.0.2.10/api/', 'http://192.0.2.10/'),
                              ('https://example.org/contest/api/', 'https://example.org/contest/')]:
            self.cfg['api_url'] = url
            args = judge.run_args(self.cfg, 1)
            self.assertIn('DOMSERVER_BASEURL=' + expected, args)
            self.assertFalse(any('DOMJUDGE_API_URL=' in a for a in args))
            self.assertIn('domjudge/judgehost:latest', args)
            self.assertIn('exec /scripts/start.sh', args[-1])

    def test_fresh_install_pulls_official_latest_and_records_actual_version(self):
        rt = MagicMock()
        rt.run.return_value = 'runguard -- part of DOMjudge version 9.0.0/release'
        record = dict(Id='sha256:selected', RepoDigests=['domjudge/judgehost@sha256:resolved'])
        with patch.object(judge, 'inspect', return_value=record):
            judge.prepare_image(rt, self.cfg)
        commands = [call.args[0] for call in rt.run.call_args_list]
        self.assertEqual(commands[0], judge.DOCKER + ['pull', '--platform', 'linux/amd64', judge.IMAGE])
        self.assertEqual(len(commands), 2)
        self.assertEqual(self.cfg['judgehost_version'], '9.0.0/release')
        self.assertEqual(self.cfg['image_digest'], record['RepoDigests'][0])
        self.assertIn(record['Id'], judge.run_args(self.cfg, 1))
        rt.artifact.assert_not_called()

    def test_retry_uses_recorded_image_even_when_latest_changes(self):
        self.cfg.update(image_ref=judge.IMAGE, image_id='sha256:original')
        rt = MagicMock()
        rt.run.return_value = 'runguard -- part of DOMjudge version 9.0.0/release'
        with patch.object(judge, 'inspect', return_value={'Id': 'sha256:original'}) as lookup:
            judge.prepare_image(rt, self.cfg)
        lookup.assert_called_once_with('image', 'sha256:original')
        self.assertEqual(rt.run.call_count, 1)
        self.assertNotIn('pull', rt.run.call_args.args[0])
        self.assertIn('sha256:original', judge.run_args(self.cfg, 1))

    def test_missing_image_restores_recorded_digest(self):
        self.cfg.update(image_ref=judge.IMAGE, image_id='sha256:original',
                        image_digest='domjudge/judgehost@sha256:old')
        rt = MagicMock()
        rt.run.return_value = 'runguard -- part of DOMjudge version 9.0.0/release'
        with patch.object(judge, 'inspect', side_effect=[None, {'Id': 'sha256:original'}]):
            judge.prepare_image(rt, self.cfg)
        self.assertEqual(rt.run.call_args_list[0].args[0][-1], 'domjudge/judgehost@sha256:old')

    def test_legacy_containers_are_never_replaced_during_retry(self):
        self.cfg['image_id'] = 'sha256:old-custom-image'
        rt = MagicMock()
        with patch.object(judge, 'inspect', return_value={'State': {'Running': True}}):
            with self.assertRaises(judge.InstallError):
                judge.prepare_image(rt, self.cfg)
        rt.run.assert_not_called()
        rt.save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
