import hashlib
import json
import pathlib
import zipfile

root = pathlib.Path(__file__).resolve().parent.parent
package = json.loads((root / "package.json").read_text(encoding="utf-8"))
output = root / "dist"
output.mkdir(exist_ok=True)
target = output / f"zotero-codex-translate-{package['version']}.xpi"
source = root / ".scaffold" / "build"
manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
assert manifest["applications"]["zotero"]["update_url"].startswith("https://")
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source).as_posix())
    for name in ["LICENSE", "THIRD_PARTY_NOTICES.md", "UPSTREAM.json"]:
        if not (source / name).exists():
            archive.write(root / name, name)
    for path in sorted((root / "third_party").rglob("*")):
        if path.is_file() and path.suffix not in (".zip", ".sha256", ".html"):
            archive.write(path, path.relative_to(root).as_posix())
    for name in ["dependency-inventory.json", "source-inventory.json", "wheels-windows-x64.json", "font-provenance.json", "dependency-inventory-linux-x64.json", "wheels-linux-x64.json"]:
        archive.write(root / "runtime" / name, "licenses/" + name)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix(".xpi.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
print(target)
