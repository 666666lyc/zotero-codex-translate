"""Run in an isolated Python 3.12 environment with bridge[build] installed."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import zipfile

root = pathlib.Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive-only", action="store_true", help="Verify an existing binary and refresh archives after documentation-only changes")
args = parser.parse_args()
version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
machine = platform.machine().lower()
arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]
label = f"zotero-codex-bridge-{version}-{system}-{arch}"
distribution = root / "dist" / label
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
           "--name", "zotero-codex-bridge", "--paths", str(root / "bridge" / "src"),
           "--distpath", str(distribution), "--workpath", str(root / "tmp" / "pyinstaller"),
           "--specpath", str(root / "tmp"), "--collect-all", "babeldoc",
           "--collect-all", "onnxruntime", "--collect-all", "pymupdf",
           "--collect-all", "bitstring",
           "--collect-all", "chardet",
           "--collect-all", "tiktoken_ext", "--copy-metadata", "babeldoc", str(root / "bridge" / "launcher.py")]
build_env = os.environ.copy()
build_env["PYINSTALLER_CONFIG_DIR"] = str(root / "tmp" / "pyinstaller-config")
if not args.archive_only:
    subprocess.run(command, check=True, cwd=root, env=build_env)
binary = distribution / "zotero-codex-bridge" / ("zotero-codex-bridge.exe" if system == "windows" else "zotero-codex-bridge")
subprocess.run([str(binary), "--help"], check=True)
smoke = subprocess.run([str(binary), "--data-dir", str(root / "tmp" / "packaged-bridge-check")],
                       input=json.dumps({"protocolVersion": 1, "id": 1, "method": "engine.check", "params": {}}) + "\n",
                       text=True, encoding="utf-8", capture_output=True, timeout=90, check=True)
messages = [json.loads(line) for line in smoke.stdout.splitlines()]
if not messages or not messages[-1].get("result", {}).get("ready"):
    raise RuntimeError("Packaged PDF engine check failed: " + smoke.stdout + smoke.stderr)
print("Packaged BabelDOC native runtime check passed", flush=True)
source_archive = root / "dist" / f"zotero-codex-translate-{version}-source.zip"
with zipfile.ZipFile(source_archive, "w", zipfile.ZIP_DEFLATED) as archive:
    for directory in ["bridge", "src", "typings", "addon", "scripts", "tests", "docs", "third_party", ".github"]:
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and not any(part in ("__pycache__", "build", "dist") or part.endswith(".egg-info") for part in path.parts):
                archive.write(path, path.relative_to(root).as_posix())
    for name in ["LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "UPSTREAM.json", "package.json", "package-lock.json", "tsconfig.json"]:
        archive.write(root / name, name)
shutil.copy(source_archive, distribution / source_archive.name)
for name in ["LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"]:
    shutil.copy(root / name, distribution / name)
(distribution / "dependencies.json").write_text(json.dumps({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}, indent=2), encoding="utf-8")
print("Writing bridge archive...", flush=True)
staging = pathlib.Path(shutil.make_archive(str(root / "tmp" / (label + "-staging")), "zip", distribution))
archive = root / "dist" / (label + ".zip")
staging.replace(archive)
archive.with_suffix(".zip.sha256").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="ascii")
print(archive)
