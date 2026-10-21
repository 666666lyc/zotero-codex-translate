"""Back up and replace this addon only after Zotero has exited. No library writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import zipfile

import psutil

ROOT = Path(__file__).resolve().parents[1]
ADDON = "codex-translate@zotero.local"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    profile = args.profile.resolve(strict=True)
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            if process.info["name"].lower() != "zotero.exe":
                continue
            command = process.info["cmdline"] or []
            if "-contentproc" not in command:
                raise RuntimeError("请先正常退出所有 Zotero 窗口，再部署插件。")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    source = ROOT / "dist/local-preview" / version / f"zotero-codex-translate-{version}.xpi"
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == version and manifest["applications"]["zotero"]["id"] == ADDON
    target = (profile / "extensions" / (ADDON + ".xpi")).resolve()
    target.relative_to(profile)
    assert target.is_file(), "This deployment helper upgrades an already installed addon only"
    backup = ROOT / "tmp" / ("local-deployment-" + version + "-" + time.strftime("%Y%m%d-%H%M%S"))
    backup.resolve().relative_to((ROOT / "tmp").resolve())
    backup.mkdir()
    shutil.copyfile(target, backup / "previous.xpi")
    for name in ("extensions.json", "addonStartup.json.lz4"):
        path = (profile / name).resolve()
        path.relative_to(profile)
        if path.is_file():
            shutil.copyfile(path, backup / name)
    pointer = args.data_dir / "codex-translate/runtime/current.json"
    if pointer.is_file():
        shutil.copyfile(pointer, backup / "runtime-before.json")
    for name in ("addonStartup.json.lz4", "startupCache"):
        path = (profile / name).resolve()
        path.relative_to(profile)
        if path.exists():
            destination = (backup / ("saved-" + name)).resolve()
            destination.relative_to(backup.resolve())
            shutil.move(str(path), str(destination))
    # Windows keeps the active startup cache under Local, separately from the
    # Roaming profile. Preserve that cache too so chrome documents refresh.
    if os.name == "nt" and os.environ.get("APPDATA") and os.environ.get("LOCALAPPDATA"):
        roaming = Path(os.environ["APPDATA"]).resolve()
        local = Path(os.environ["LOCALAPPDATA"]).resolve()
        if profile.is_relative_to(roaming):
            local_profile = (local / profile.relative_to(roaming)).resolve()
            local_profile.relative_to(local)
            cache = (local_profile / "startupCache").resolve()
            cache.relative_to(local_profile)
            if cache.is_dir():
                destination = (backup / "saved-local-startupCache").resolve()
                destination.relative_to(backup.resolve())
                shutil.move(str(cache), str(destination))
    # copyfile supplies a new modification time so AddonManager rescans manifest.
    shutil.copyfile(source, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
    result = {"profile": str(profile), "backup": str(backup), "version": version,
              "installed": str(target), "sha256": digest}
    (backup / "deployment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
