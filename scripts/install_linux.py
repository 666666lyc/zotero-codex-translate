"""Install the Linux x64 bridge in the Zotero data directory (Python 3.12).

Run after npm run build. System Python and packages are never changed.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True, help='Zotero data directory / codex-translate')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 12):
        parser.error('Requires Linux x86_64 and Python 3.12')
    root = args.data_dir.resolve() / 'runtime'
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'linux-install.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = root / 'linux-env'
        python = target / 'bin/python'
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(target)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PYTHON', 'PIP_', 'UV_', 'VIRTUAL_ENV', 'CONDA_'))}
        assets = root.parent / 'engine-assets'
        env.update(PIP_CACHE_DIR=str(root / 'pip-cache'), ZOTERO_CODEX_ASSET_DIR=str(assets), ZOTERO_CODEX_MANAGED='1', PYTHONUTF8='1')
        def run(*arguments):
            subprocess.run([str(python), *arguments], env=env, check=True)
        run('-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '--require-hashes', '--only-binary=:all:', '-r', str(ROOT / 'runtime/requirements-linux-x64.lock'))
        bundle = ROOT / '.scaffold/build/content/runtime'
        manifest = json.loads((bundle / 'manifest.json').read_text())
        wheel = bundle / manifest['bridgeWheel']
        import hashlib
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest['bundleHashes'][wheel.name]:
            raise ValueError('Bridge wheel digest mismatch; rebuild first')
        run('-m', 'pip', '--isolated', 'install', '--no-deps', '--force-reinstall', str(wheel))
        run('-m', 'pip', '--isolated', 'check')
        run('-I', '-c', 'from zotero_codex_bridge.resources import prepare; import sys; prepare(sys.argv[1], lambda event: print(event, flush=True))', str(assets))
        run('-I', '-c', 'from zotero_codex_bridge.pdf_engine import check_runtime; import sys; print(check_runtime(sys.argv[1]))', str(assets))
        record = dict(runtimeRoot=str(root), python=str(python), resourceDir=str(assets), bridgeVersion=manifest['bridgeVersion'])
        temporary = root / 'linux-current.tmp'
        temporary.write_text(json.dumps(record, indent=2) + '\n')
        os.replace(temporary, root / 'linux-current.json')
        print('Linux bridge ready:', python)


if __name__ == '__main__':
    main()
