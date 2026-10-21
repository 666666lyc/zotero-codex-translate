"""Exercise the XPI installer with empty cache and no system Python dependency.

The invoking Python is only the test harness. The installer uses the downloaded
uv and its downloaded CPython. Does not require a Codex account.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--cancel-after", type=int)
    args = parser.parse_args()
    root = args.root.resolve() if args.root else ROOT / "tmp" / ("runtime-cold-" + uuid.uuid4().hex[:8]) / "中文 data" / "codex-translate" / "runtime"
    root.relative_to((ROOT / "tmp").resolve())
    root.mkdir(parents=True, exist_ok=True)
    bundle = ROOT / ".scaffold/build/content/runtime"
    manifest = json.loads((bundle / "manifest.json").read_text())
    start = time.monotonic()
    archive = root / "uv.zip"
    if not archive.exists():
        urllib.request.urlretrieve(manifest["uv"]["url"], archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["uv"]["sha256"]
    uv = root / "uv.exe"
    with zipfile.ZipFile(archive) as z:
        uv.write_bytes(z.read("uv.exe"))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("UV_", "PIP_", "PYTHON", "VIRTUAL_ENV", "CONDA_"))}
    env.update(UV_CACHE_DIR=str(root / "cache"), UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_PYTHON_DOWNLOADS_JSON_URL=str(bundle / "python-downloads.json"), UV_PYTHON_INSTALL_BIN="0", UV_PYTHON_INSTALL_REGISTRY="0", UV_NO_CONFIG="1")
    print("ROOT", root, flush=True)
    subprocess.run([str(uv), "--no-config", "python", "install", "3.12.15", "--no-bin", "--no-registry"], env=env, check=True)
    python = root / "python/cpython-3.12.15-windows-x86_64-none/python.exe"
    child = subprocess.Popen([str(python), "-I", str(bundle / "install.py"), "--root", str(root), "--bundle", str(bundle), "--uv", str(uv), *(["--repair"] if args.repair else [])], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")
    timer = None
    if args.cancel_after:
        import threading
        timer = threading.Timer(args.cancel_after, child.stdin.close)
        timer.start()
    last = ""
    result = None
    for line in child.stdout:
        try:
            event = json.loads(line)
            if event.get("stage") == "resources":
                key = "resources " + str(int(100 * event["completed"] / event["total"]))
            else:
                key = line.rstrip()
            if key != last:
                print(key[:1600], flush=True)
                last = key
            if event.get("result"):
                result = event["result"]
        except ValueError:
            print(line.rstrip(), flush=True)
    code = child.wait()
    child.stdout.close()
    if not child.stdin.closed:
        child.stdin.close()
    if timer:
        timer.cancel()
    report = dict(passed=code == 0 and bool(result), cancelled=bool(args.cancel_after), seconds=round(time.monotonic() - start, 1), result=result, installedBytes=sum(p.stat().st_size for p in root.parent.rglob("*") if p.is_file()))
    (root.parent / "runtime-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
