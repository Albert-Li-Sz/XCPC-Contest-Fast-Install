import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import installer
import ui
import batch_wizard


def terminal_run(command, exchanges):
    """Exercise a real controlling TTY; never execute an installation in these tests."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    output = b''
    cursor = 0
    deadline = time.monotonic() + 20
    finished = False
    try:
        while time.monotonic() < deadline:
            if cursor < len(exchanges) and exchanges[cursor][0].encode() in output:
                os.write(fd, exchanges[cursor][1].encode())
                cursor += 1
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(fd, 16384)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
        else:
            raise AssertionError('Interactive test timed out: ' + output.decode(errors='replace'))
        _, status = os.waitpid(pid, 0)
        finished = True
        return os.waitstatus_to_exitcode(status), output.decode(errors='replace')
    finally:
        os.close(fd)
        if not finished:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def args(role):
    return argparse.Namespace(role=role, host=None, api_url=None, hostname=None, cpus=None,
                              api_user=None, api_password_file=None, api_ca_file=None,
                              timezone=None, contest_id=None, artifact_dir=None, yes=False)


class Interactive(unittest.TestCase):
    def test_bash_process_substitution_reaches_real_menu_and_exits(self):
        code, output = terminal_run(['bash', '-c', 'bash <(cat "$1")', 'test', str(ROOT / 'main.sh')],
                                    [('请选择', '9\n'), ('选项无效', '0\n')])
        self.assertEqual(code, 0, output)
        self.assertIn('中文交互菜单', output)
        self.assertNotIn('正在执行：', output)

    def test_secret_input_does_not_echo_on_real_tty(self):
        code, output = terminal_run([sys.executable, '-c',
            'import sys;sys.path.insert(0,sys.argv[1]);from ui import ask;'
            's=ask("HIDDEN_PROMPT",secret=True);print("READ_LENGTH",len(s))', str(ROOT / 'src')],
            [('HIDDEN_PROMPT', 'fixture-hidden-password\n')])
        self.assertEqual(code, 0, output)
        self.assertIn('READ_LENGTH 23', output)
        self.assertNotIn('fixture-hidden-password', output)

    def test_address_shortcuts_do_not_accept_credential_urls(self):
        self.assertEqual(ui.api_address('192.0.2.10'), 'http://192.0.2.10/api/')
        self.assertEqual(ui.api_address('https://judge.example.org/api'), 'https://judge.example.org/api/')
        with self.assertRaises(installer.InstallError):
            ui.api_address('https://admin:secret@judge.example.org/api/')

    def test_server_invalid_address_reprompts(self):
        settings = args('server')
        with patch.object(ui, 'ask', side_effect=['bad;host', '192.0.2.10', '1']), \
             patch.object(installer, 'guessed_ip', return_value='192.0.2.9'), \
             patch.object(installer, 'configuration', return_value={'role': 'server'}) as config, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            installer.interactive_settings(settings)
        self.assertEqual(settings.host, '192.0.2.10')
        self.assertIn('输入无效', out.getvalue())
        config.assert_called_once()

    def test_judge_wizard_reprompts_offline_cpu_and_keeps_secret_out_of_summary(self):
        settings = args('judgehost')
        values = ['192.0.2.10', 'judge01', '42', '1,3', '1', 'fixture-password']
        with patch.object(ui, 'ask', side_effect=values), patch.object(ui, 'online_cpus', return_value={0, 1, 3}), \
             patch.object(Path, 'is_file', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            cfg = installer.interactive_settings(settings)
            installer.show_summary(cfg, settings)
        self.assertEqual(cfg['cpus'], [1, 3])
        self.assertEqual(settings._api_password, 'fixture-password')
        self.assertNotIn('fixture-password', str(cfg))
        self.assertNotIn('fixture-password', out.getvalue())

    def test_existing_server_menu_does_not_offer_judgehost_install(self):
        with patch.object(ui, 'choose', return_value='4') as choose:
            installer.main_menu({'config': {'role': 'server'}, 'complete': True})
        options = dict(choose.call_args.args[1])
        self.assertIn('1', options)
        self.assertNotIn('2', options)

    def test_embedded_batch_entry_is_exactly_the_same_script(self):
        main = (ROOT / 'main.sh').read_bytes()
        encoded = main.decode().split("<<'XCPC_PAYLOAD'\n", 1)[1].split('\nXCPC_PAYLOAD', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / 'origin'
            (origin / 'batch').mkdir(parents=True)
            (origin / 'payload.tar.gz').write_bytes(base64.b64decode(encoded))
            (origin / 'bootstrap.txt').write_text((ROOT / 'src/entry.sh').read_text())
            (origin / 'batch/deploy.yml').write_text((ROOT / 'batch/deploy.yml').read_text())
            destination = root / 'target'
            destination.mkdir()
            with patch.object(batch_wizard, 'ROOT', origin):
                batch_wizard.stage_project(destination)
            self.assertEqual((destination / 'main.sh').read_bytes(), main)

    def test_batch_cancellation_never_starts_ansible_or_ssh(self):
        fixture = {'all': {'children': {'judgehosts': {'vars': {'judge_api_url': 'http://192.0.2.10/api/'},
                   'hosts': {'judge01': {'ansible_host': '192.0.2.21', 'judge_cpus': '1'}}}}}}
        with patch.object(batch_wizard, 'collect', return_value=(fixture, {'judge_api_password': 'fixture'}, 1)), \
             patch.object(ui, 'choose', return_value='0'), patch.object(batch_wizard, 'deploy') as deploy, \
             patch.object(batch_wizard.shutil, 'which', return_value='/fake/bin'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch_wizard.main(), 0)
        deploy.assert_not_called()

    def test_batch_secrets_stay_in_private_files_and_cleanup_after_failure(self):
        inventory = {'all': {'children': {'judgehosts': {'vars': {}, 'hosts': {}}}}}
        private = {'judge_api_password': 'fixture-api-password', 'ansible_password': 'fixture-ssh-password'}
        staged = []
        def command(argv, **kwargs):
            self.assertNotIn('fixture-api-password', str(argv))
            self.assertNotIn('fixture-ssh-password', str(argv))
            directory = Path(kwargs['cwd'])
            staged.append(directory)
            file = directory / 'credentials.json'
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(file.read_text()), private)
            return 2
        with patch.object(batch_wizard, 'ansible_command', return_value='ansible-playbook'), \
             patch.object(batch_wizard, 'verify_host_keys', return_value='-o StrictHostKeyChecking=yes'), \
             patch.object(batch_wizard.subprocess, 'call', side_effect=command):
            self.assertEqual(batch_wizard.deploy(inventory, private, 1), 2)
        self.assertFalse(staged[0].exists())


if __name__ == '__main__':
    unittest.main()
