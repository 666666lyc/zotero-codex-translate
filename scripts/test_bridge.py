import os
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
env = os.environ.copy()
env["PYTHONPATH"] = str(root / "bridge" / "src")
temporary_root = root / "tmp" / "bridge-tests"
temporary_root.mkdir(parents=True, exist_ok=True)
for variable in ("TMP", "TEMP", "TMPDIR"):
    env[variable] = str(temporary_root)
raise SystemExit(subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "bridge/tests", "-v"], cwd=root, env=env))
