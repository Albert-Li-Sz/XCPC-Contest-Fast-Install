#!/usr/bin/env python3
"""Build a self-contained main.sh; no server files or secret files are inputs."""
import argparse
import base64
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    files = {"bootstrap.txt": (ROOT / "src/entry.sh").read_bytes()}
    for name in ["deploy.yml", "README.md", "inventory.example.yml", "start.sh"]:
        files["batch/" + name] = (ROOT / "batch" / name).read_bytes()
    for base, prefix in [(ROOT / "src", ""), (ROOT / "docs", "docs/")]:
        for file in sorted(base.rglob("*")):
            if not file.is_file() or file.name == "entry.sh":
                continue
            if file.is_symlink():
                raise ValueError("Build inputs must not be symlinks")
            relative = file.relative_to(base)
            if any(part in {"__pycache__", ".ansible", "logs", ".venv"} for part in relative.parts):
                continue
            if file.suffix in {".secret", ".pyc", ".key", ".p12"} or file.name == "inventory.yml":
                raise ValueError(f"Refusing sensitive/build-only input: {relative}")
            files[prefix + relative.as_posix()] = file.read_bytes()
    return files


def build():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        for name, data in sorted(inputs().items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
            bundle.addfile(entry, io.BytesIO(data))
    compressed = gzip.compress(buffer.getvalue(), mtime=0)
    content = (ROOT / "src/entry.sh").read_text().replace(
        "@@PAYLOAD@@", "\n".join(textwrap.wrap(base64.b64encode(compressed).decode(), 100))
    ).replace("@@PAYLOAD_SHA@@", hashlib.sha256(compressed).hexdigest())
    (ROOT / "main.sh").write_text(content)
    (ROOT / "main.sh").chmod(0o755)


def check():
    content = (ROOT / "main.sh").read_text()
    encoded = content.split("<<'XCPC_PAYLOAD'\n", 1)[1].split("\nXCPC_PAYLOAD", 1)[0]
    data = base64.b64decode(encoded, validate=False)
    match = re.search(r'python3 - "\$XCPC_TMP/payload.tar.gz" "([a-f0-9]{64})"', content)
    if not match:
        raise ValueError("Embedded checksum is missing")
    expected = match[1]
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Embedded payload checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
        actual = {m.name: bundle.extractfile(m).read() for m in bundle}
    if actual != inputs():
        raise ValueError("main.sh is stale; run python3 scripts/build.py")
    # Also validate the bootstrap, not just the archive.
    template = (ROOT / "src/entry.sh").read_text()
    rebuilt = template.replace("@@PAYLOAD@@", encoded).replace("@@PAYLOAD_SHA@@", expected)
    if rebuilt != content:
        raise ValueError("Bootstrap is stale")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else build()
    print("Self-contained main.sh: OK")
