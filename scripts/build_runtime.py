"""Build a reproducible pure-Python wheel and the small XPI runtime bundle."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile
import csv
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    destination = ROOT / ".scaffold/build/content/runtime"
    destination.mkdir(parents=True, exist_ok=True)
    dist_info = f"zotero_codex_bridge-{version}.dist-info"
    files = {str(p.relative_to(ROOT / "bridge/src")).replace("\\", "/"): p.read_bytes() for p in (ROOT / "bridge/src/zotero_codex_bridge").iterdir() if p.suffix in (".py", ".json")}
    files[f"{dist_info}/METADATA"] = f"Metadata-Version: 2.4\nName: zotero-codex-bridge\nVersion: {version}\nRequires-Python: >=3.12,<3.14\nLicense-Expression: AGPL-3.0-only\nLicense-File: LICENSE\n\nLocal stdio PDF translation bridge.\n".encode()
    dependencies = tomllib.loads((ROOT / "bridge/pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    files[f"{dist_info}/METADATA"] = files[f"{dist_info}/METADATA"].replace(b"\n\nLocal", ("\n" + "".join(f"Requires-Dist: {dependency}\n" for dependency in dependencies) + "\nLocal").encode())
    files[f"{dist_info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: zotero-codex build_runtime.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[f"{dist_info}/licenses/LICENSE"] = (ROOT / "LICENSE").read_bytes()
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, data in sorted(files.items()):
        writer.writerow([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="), len(data)])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()
    wheel = f"zotero_codex_bridge-{version}-py3-none-any.whl"
    with zipfile.ZipFile(destination / wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    for name in ["install.py", "requirements-windows-x64.lock", "python-downloads.json"]:
        shutil.copyfile(ROOT / "runtime" / name, destination / name)
    names = [wheel, "install.py", "requirements-windows-x64.lock", "python-downloads.json"]
    hashes = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in names}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:20]
    manifest = dict(schemaVersion=1, bridgeVersion=version, environmentVersion=f"{version}-{identity}", bridgeWheel=wheel, bundleHashes=hashes, platforms=["windows-x64"], uv=dict(version="0.13.4", url="https://github.com/astral-sh/uv/releases/download/0.13.4/uv-x86_64-pc-windows-msvc.zip", sha256="ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225", size=15237259), python=dict(version="3.12.15", build="20261013", sha256="7a2aef48e5ee569e310b29b32681debbc5d888e92790d85331a47736c6b06aad"))
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Runtime bundle:", identity, sum(p.stat().st_size for p in destination.iterdir()), "bytes")


if __name__ == "__main__":
    main()
