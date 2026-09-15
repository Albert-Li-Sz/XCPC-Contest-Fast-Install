import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import common
import installer
import judge_dependencies as dependencies


class Platforms(unittest.TestCase):
    def check_platform(self, role, distro, version):
        with patch.object(installer.os, 'geteuid', return_value=0), \
             patch.object(installer.platform, 'system', return_value='Linux'), \
             patch.object(installer.platform, 'machine', return_value='x86_64'), \
             patch.object(Path, 'is_dir', return_value=True), \
             patch.object(installer, 'system_release', return_value={'ID': distro, 'VERSION_ID': version}):
            return installer.platform_check(role)

    def test_server_runtime_and_java_executable_for_each_supported_system(self):
        for distro, version, java in [('debian', '12', 17), ('debian', '13', 21),
                                     ('ubuntu', '24.04', 21), ('ubuntu', '26.04', 21)]:
            with self.subTest(distro=distro, version=version):
                self.check_platform('server', distro, version)
                runtime = common.server_runtime(distro, version)
                self.assertEqual(runtime['java_major'], java)
                unit = common.render('icpc-live.service', JAVA_HOME=runtime['java_home'], TIMEZONE='UTC')
                self.assertIn(f'ExecStart=/usr/lib/jvm/java-{java}-openjdk-amd64/bin/java ', unit)

    def test_unknown_distro_is_allowed_for_judge_but_not_server(self):
        for distro, version in [('fedora', '44'), ('arch', ''), ('custom-linux', 'unknown')]:
            self.check_platform('judgehost', distro, version)
            with self.assertRaises(common.InstallError):
                self.check_platform('server', distro, version)

    def test_missing_release_file_does_not_block_judge(self):
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            release = common.system_release()
        self.check_platform('judgehost', release['ID'], release['VERSION_ID'])


class JudgeDependencies(unittest.TestCase):
    def test_reuses_dependencies_without_a_recognized_package_manager(self):
        with patch.object(dependencies.shutil, 'which', side_effect=lambda name: name if name in {'docker', 'chronyc', 'curl'} else None):
            rt = MagicMock()
            dependencies.install_missing(rt)
            rt.run.assert_not_called()

    def test_missing_dependencies_are_reported_on_unknown_package_manager(self):
        with patch.object(dependencies.shutil, 'which', return_value=None):
            rt = MagicMock()
            with self.assertRaisesRegex(common.InstallError, '缺少依赖.*docker.*chronyc.*curl'):
                dependencies.install_missing(rt)
            rt.run.assert_not_called()

    def test_package_managers_install_only_missing_engine(self):
        for manager, engine in [('apt-get', 'docker.io'), ('dnf', 'docker-ce'),
                                ('yum', 'moby-engine'), ('zypper', 'docker'), ('pacman', 'docker')]:
            with self.subTest(manager=manager):
                available = {manager, 'chronyc', 'curl'}
                rt = MagicMock()
                rt.run.side_effect = lambda args: available.add('docker') if engine in args else None
                with patch.object(dependencies.shutil, 'which', side_effect=lambda name: name if name in available else None), \
                     patch.object(dependencies, 'rpm_docker_package', return_value=engine):
                    dependencies.install_missing(rt)
                commands = [call.args[0] for call in rt.run.call_args_list]
                self.assertTrue(any(engine in cmd for cmd in commands))
                self.assertFalse(any('chrony' in cmd or 'curl' in cmd for cmd in commands))
                self.assertFalse(any('upgrade' in cmd or '-Syu' in cmd for cmd in commands))

    def test_missing_chrony_does_not_replace_existing_docker(self):
        available = {'docker', 'apt-get', 'curl'}
        rt = MagicMock()
        rt.run.side_effect = lambda args: available.add('chronyc') if 'chrony' in args else None
        with patch.object(dependencies.shutil, 'which', side_effect=lambda name: name if name in available else None):
            dependencies.install_missing(rt)
        self.assertIn('chrony', rt.run.call_args.args[0])
        self.assertNotIn('docker.io', rt.run.call_args.args[0])

    def test_successful_package_command_must_provide_binaries(self):
        with patch.object(dependencies.shutil, 'which', side_effect=lambda name: name if name == 'apt-get' else None):
            with self.assertRaisesRegex(common.InstallError, '仍缺少'):
                dependencies.install_missing(MagicMock())

    def test_rpm_requires_real_engine_package_in_configured_repository(self):
        responses = [subprocess.CompletedProcess([], 0, 'No matching Packages to list', ''),
                     subprocess.CompletedProcess([], 0, 'moby-engine.x86_64 28.0 distro', '')]
        with patch.object(dependencies.subprocess, 'run', side_effect=responses):
            self.assertEqual(dependencies.rpm_docker_package('dnf'), 'moby-engine')
        with patch.object(dependencies.subprocess, 'run', return_value=responses[0]):
            with self.assertRaisesRegex(common.InstallError, '软件源'):
                dependencies.rpm_docker_package('dnf')

    def test_chronyd_service_is_used_on_rpm_systems(self):
        with patch.object(Path, 'is_file', lambda path: str(path) == '/usr/lib/systemd/system/chronyd.service'), \
             patch.object(dependencies, 'install_missing'), patch.object(dependencies.shutil, 'which', return_value=None):
            rt, cfg = MagicMock(), {'timezone': 'UTC'}
            dependencies.prepare(rt, cfg)
            self.assertEqual(cfg['chrony_service'], 'chronyd')
            rt.run.assert_any_call(['systemctl', 'enable', '--now', 'docker', 'chronyd'])


class LegacyTar(unittest.TestCase):
    def extract(self, entries, base):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as bundle:
            for name, kind, content in entries:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                if kind == tarfile.DIRTYPE:
                    entry.mode = 0o755
                if kind == tarfile.REGTYPE:
                    raw = content.encode()
                    entry.size, entry.mode = len(raw), 0o755
                    bundle.addfile(entry, io.BytesIO(raw))
                else:
                    entry.linkname = content
                    bundle.addfile(entry)
        data.seek(0)
        with tarfile.open(fileobj=data) as bundle:
            common.extract_tar_legacy(bundle, base)

    def test_relative_forward_links_and_executables_extract_on_old_python(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'target'
            self.extract([('app/link', tarfile.SYMTYPE, 'bin/tool'),
                          ('app/bin/tool', tarfile.REGTYPE, '#!/bin/sh\nexit 0\n')], base)
            self.assertEqual((base / 'app/link').read_text(), '#!/bin/sh\nexit 0\n')
            self.assertTrue((base / 'app/bin/tool').stat().st_mode & 0o111)

    def test_directories_remain_traversable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'target'
            previous = os.umask(0o077)
            try:
                self.extract([('app', tarfile.DIRTYPE, ''),
                              ('app/file', tarfile.REGTYPE, 'data')], base)
            finally:
                os.umask(previous)
            self.assertEqual((base / 'app').stat().st_mode & 0o777, 0o755)

    def test_unsafe_names_types_and_symlink_parents_rejected(self):
        cases = [[('../escape', tarfile.REGTYPE, 'bad')], [('/escape', tarfile.REGTYPE, 'bad')],
                 [('link', tarfile.SYMTYPE, '../../escape')], [('device', tarfile.CHRTYPE, '')],
                 [('pipe', tarfile.FIFOTYPE, '')], [('hard', tarfile.LNKTYPE, 'target')],
                 [('a', tarfile.REGTYPE, '1'), ('a', tarfile.REGTYPE, '2')],
                 [('alias', tarfile.SYMTYPE, 'dir'), ('alias/file', tarfile.REGTYPE, 'bad')]]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(common.InstallError):
                    self.extract(entries, Path(directory) / 'target')

    def test_existing_symlink_cannot_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'target'
            base.mkdir()
            (base / 'alias').symlink_to(directory)
            with self.assertRaises(common.InstallError):
                self.extract([('alias/escape', tarfile.REGTYPE, 'bad')], base)
            self.assertFalse((Path(directory) / 'escape').exists())

    def test_forward_link_chain_cannot_escape_or_form_cycles(self):
        for entries in [[('a', tarfile.SYMTYPE, 'b/../../escape'), ('b', tarfile.SYMTYPE, '.')],
                        [('a', tarfile.SYMTYPE, 'b'), ('b', tarfile.SYMTYPE, 'a')]]:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(common.InstallError):
                    self.extract(entries, Path(directory) / 'target')


if __name__ == '__main__':
    unittest.main()
