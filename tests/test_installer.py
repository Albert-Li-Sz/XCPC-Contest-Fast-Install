import base64
import hashlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import common
from common import InstallError


class Inputs(unittest.TestCase):
    def test_hostname_and_ipv4(self):
        for value, result in [("192.0.2.10", "192.0.2.10"), ("Judge.Example.org", "judge.example.org")]:
            self.assertEqual(common.host(value), result)

    def test_config_injection_rejected(self):
        for value in ["x; include /tmp/evil;", "a\nb", "$(id)", "https://a", "a/path",
                      "999.1.2.3", "0.0.0.0", "::1", "-a", "a-", "a..b", "a:80"]:
            with self.subTest(value=value), self.assertRaises(InstallError):
                common.host(value)

    def test_api_credentials_cannot_be_in_url(self):
        for value in ["http://a:b@host/api/", "http://host/", "http://host/api/?x=1",
                      "http://host/api/#f", "file:///api/", "http://host/a b/api/"]:
            with self.subTest(value=value), self.assertRaises(InstallError):
                common.api_url(value)
        self.assertEqual(common.api_url("https://judge.example.org/api/"),
                         "https://judge.example.org/api/")

    def test_cpu_selection(self):
        self.assertEqual(common.cpu_list("0,2,5"), [0, 2, 5])
        for value in ["1,1", "1-3", "-1", "1, 2", ""]:
            with self.assertRaises(InstallError):
                common.cpu_list(value)


class InstallationSafety(unittest.TestCase):
    def test_failed_phase_is_not_marked_complete(self):
        rt = common.Runtime({"done": []}, Path("/unused"))
        with patch.object(rt, "save") as save:
            with self.assertRaises(RuntimeError):
                rt.step("database", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
            self.assertEqual(rt.state["done"], [])
            save.assert_not_called()

    def test_completed_phase_does_not_run_again(self):
        rt = common.Runtime({"done": ["database"]}, Path("/unused"))
        with patch.object(rt, "save"), patch("builtins.print"):
            def forbidden():
                self.fail("Database install must not run again")
            rt.step("database", forbidden)

    def test_zip_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "bad")
            with self.assertRaises(InstallError):
                common.extract_zip(archive, base / "target")
            self.assertFalse((base / "escape").exists())

    def test_tar_absolute_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.tar"
            with tarfile.open(archive, "w") as bundle:
                entry = tarfile.TarInfo("bad-link")
                entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
                bundle.addfile(entry)
            with self.assertRaises(InstallError):
                common.extract_tar(archive, Path(directory) / "target")

    def test_local_artifact_checksum_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "in").mkdir()
            (base / "cache").mkdir()
            (base / "in/file.tar.gz").write_bytes(b"corrupt")
            rt = common.Runtime({"done": []}, base / "log", base / "in")
            with patch.object(common, "CACHE", base / "cache"), patch.dict(
                common.RELEASES, {"test": ("file.tar.gz", "https://example.invalid/", "0" * 64)}
            ):
                with self.assertRaises(InstallError):
                    rt.artifact("test")
                self.assertFalse((base / "cache/file.tar.gz").exists())


class Bootstrap(unittest.TestCase):
    def test_generated_payload_matches_sources(self):
        subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--check"], check=True)

    def test_bash_process_substitution_help(self):
        # Exercise the documented invocation shape, without touching a Linux host.
        result = subprocess.run(["bash", "-c", 'bash <(cat "$1") --help', "test", str(ROOT / "main.sh")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DOMjudge + CDS + Live", result.stdout)

    def test_truncated_download_cannot_execute(self):
        data = (ROOT / "main.sh").read_text()
        partial = data.split("\nXCPC_PAYLOAD\n", 1)[0]
        result = subprocess.run(["bash"], input=partial, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("准备入口依赖", result.stdout)

    def invalid_payload(self, entry):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as bundle:
            bundle.addfile(entry)
        payload = gzip.compress(data.getvalue(), mtime=0)
        script = (ROOT / 'src/entry.sh').read_text().replace(
            '@@PAYLOAD@@', base64.b64encode(payload).decode()).replace(
            '@@PAYLOAD_SHA@@', hashlib.sha256(payload).hexdigest())
        return subprocess.run(['bash'], input=script, text=True, capture_output=True)

    def test_bootstrap_rejects_parent_paths_even_with_valid_checksum(self):
        result = self.invalid_payload(tarfile.TarInfo('../escaped'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('非法文件或路径', result.stderr)

    def test_bootstrap_rejects_symlinks_even_with_valid_checksum(self):
        entry = tarfile.TarInfo('alias')
        entry.type, entry.linkname = tarfile.SYMTYPE, '/tmp'
        result = self.invalid_payload(entry)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('非法文件或路径', result.stderr)

    def test_nginx_templates_keep_sensitive_routes_protected(self):
        # Verify security intent at the template boundary, without mirroring every line.
        live = common.render("live-nginx.conf", HOST="192.0.2.10")
        self.assertIn('proxy_set_header Authorization "";', live)
        self.assertIn("proxy_ssl_verify on;", live)
        self.assertIn("limit_except GET { deny all; }", live)
        dom = common.render("domjudge-nginx-inner.conf", HOST="judge.example.org")
        self.assertIn('set $prefix "";', dom)
        self.assertIn("location ~ /\\. { return 404; }", dom)


if __name__ == "__main__":
    unittest.main(verbosity=2)
