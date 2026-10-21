"""Runs with the private standalone Python, using only its standard library.

Input EOF cancels preparation. stdout is JSON Lines; no account is required.
Environment directories are created at their final paths and never renamed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


STOP = threading.Event()
CHILD = None
STATE = None
LAST_STATE = 0


def emit(**value):
    global LAST_STATE
    if STATE and (time.monotonic() - LAST_STATE > 1 or value.get("stage") in ("ready", "failed", "cancelled")):
        atomic(STATE, value)
        LAST_STATE = time.monotonic()
    print(json.dumps(value, ensure_ascii=False), flush=True)


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def confined(root, value):
    target = Path(value).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path outside managed runtime")
    return target


def run(command, env, stage):
    global CHILD
    if STOP.is_set():
        raise InterruptedError("环境准备已取消")
    emit(stage=stage)
    # Do not inherit a pipe being read by the cancellation thread: on Windows,
    # a child CPython can otherwise wait in native stdio initialization.
    CHILD = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    child = CHILD
    def timeout():
        if child.poll() is None:
            if os.name == "nt":
                subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"), "/PID", str(child.pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                child.kill()
    timer = threading.Timer(600 if stage == "checking" else 1800, timeout)
    timer.daemon = True
    timer.start()
    lines = []
    for line in CHILD.stdout:
        lines.append(line.rstrip())
        lines = lines[-12:]
        # uv reports real per-package status; no synthetic aggregate percent.
        emit(stage=stage, detail=line.rstrip()[:1000])
    code = CHILD.wait()
    timer.cancel()
    CHILD.stdout.close()
    CHILD = None
    if STOP.is_set():
        raise InterruptedError("环境准备已取消")
    if code:
        raise RuntimeError(f"{stage} (exit {code}): " + "\n".join(lines)[-5000:])


def watch_input():
    sys.stdin.read()
    STOP.set()
    child = CHILD
    if child and child.poll() is None:
        if os.name == "nt":
            subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"), "/PID", str(child.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            child.terminate()


def main():
    global STATE
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # OS lock is automatically released on crashes; stale lock files are safe.
    lock = (root / "install.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Thread(target=watch_input, daemon=True).start()
    manifest = json.loads((args.bundle / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["bundleHashes"].items():
        path = confined(args.bundle, args.bundle / name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"安装文件摘要不符：{name}")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("UV_", "PIP_", "PYTHON", "VIRTUAL_ENV", "CONDA_"))}
    env.update(UV_CACHE_DIR=str(root / "cache"), UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_PYTHON_INSTALL_BIN="0", UV_PYTHON_INSTALL_REGISTRY="0", UV_NO_PROGRESS="1", UV_NO_CONFIG="1", UV_INDEX_URL="https://pypi.org/simple", UV_PYTHON_DOWNLOADS="never", UV_SYSTEM_CERTS="true", UV_HTTP_RETRIES="2", PYTHONUTF8="1", ZOTERO_CODEX_MANAGED="1")
    # All temp files remain inside the managed directory, including uv temps.
    temporary = root / "tmp"
    temporary.mkdir(exist_ok=True)
    env.update(TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    state = root / "state.json"
    STATE = state
    target = root / "envs" / (manifest["environmentVersion"] + "-" + uuid.uuid4().hex[:8])
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assets = root.parent / "engine-assets"
    env["ZOTERO_CODEX_ASSET_DIR"] = str(assets)
    try:
        current_file = root / "current.json"
        try:
            current = json.loads(current_file.read_text(encoding="utf-8")) if current_file.exists() else None
        except (ValueError, OSError):
            current = None
        pending_file = root / "pending.json"
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8")) if pending_file.exists() else None
        except (ValueError, OSError):
            pending = None
        if pending and pending.get("environmentVersion") == manifest["environmentVersion"] and pending.get("runtimeRoot") == str(root):
            candidate = confined(root, pending["python"])
            if not current or str(candidate) != current.get("python"):
                python = candidate
                target = candidate.parent.parent
        if not args.repair and current and current.get("environmentVersion") == manifest["environmentVersion"] and current.get("runtimeRoot") == str(root):
            candidate = confined(root, current["python"])
            if candidate.is_file():
                python = candidate
                target = candidate.parent.parent
        atomic(pending_file, dict(environmentVersion=manifest["environmentVersion"], runtimeRoot=str(root), python=str(python)))
        atomic(state, dict(stage="dependencies", environmentVersion=manifest["environmentVersion"], python=str(python)))
        if not python.exists():
            run([str(args.uv), "--no-config", "venv", "--python", sys.executable, "--no-python-downloads", str(target)], env, "venv")
        run([str(args.uv), "--no-config", "pip", "sync", "--python", str(python), "--require-hashes", "--only-binary", ":all:", "--index-url", "https://pypi.org/simple", *(["--reinstall"] if args.repair else []), str(args.bundle / "requirements-windows-x64.lock")], env, "dependencies")
        run([str(args.uv), "--no-config", "pip", "install", "--python", str(python), "--no-deps", "--reinstall", str(args.bundle / manifest["bridgeWheel"])], env, "bridge")
        run([str(args.uv), "--no-config", "pip", "check", "--python", str(python)], env, "checking")
        # Run in this process so the same cancellation event controls resources.
        sys.path.insert(0, str(target / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")))
        from zotero_codex_bridge.resources import prepare
        resource_result = prepare(assets, lambda event: emit(**event), STOP)
        code = "from zotero_codex_bridge.pdf_engine import check_runtime; import json,sys; print(json.dumps(check_runtime(sys.argv[1])))"
        run([str(python), "-I", "-c", code, str(assets)], env, "checking")
        # A fresh stdio hello additionally verifies protocol and packaging.
        probe = subprocess.run([str(python), "-I", "-m", "zotero_codex_bridge", "--data-dir", str(root / "probe")], input='{"protocolVersion":1,"id":1,"method":"hello"}\n', capture_output=True, text=True, encoding="utf-8", env=env, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        hello = json.loads(probe.stdout.splitlines()[0])["result"]
        if probe.returncode or hello["version"] != manifest["bridgeVersion"] or hello["protocolVersion"] != 1:
            raise RuntimeError("桥接协议检查失败")
        if STOP.is_set():
            raise InterruptedError("环境准备已取消")
        result = dict(environmentVersion=manifest["environmentVersion"], runtimeRoot=str(root), python=str(python), resourceDir=str(assets), bridgeVersion=hello["version"], preparedAt=time.time(), resources=resource_result)
        if current and current.get("python") != str(python):
            atomic(root / "previous.json", current)
        atomic(current_file, result)
        pending_file.unlink(missing_ok=True)
        atomic(state, dict(stage="ready", **result))
        emit(stage="ready", result=result)
    except Exception as error:
        stage = "cancelled" if STOP.is_set() else "failed"
        atomic(state, dict(stage=stage, error=f"{type(error).__name__}: {error}", environmentVersion=manifest["environmentVersion"]))
        emit(stage=stage, error=f"{type(error).__name__}: {error}")
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
