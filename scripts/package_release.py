"""Allowlisted corresponding-source release; no runtime or personal artifacts.

--public requires a configured real repository and an existing matching tag.
Without it this produces a LOCAL preview, never a claim of public release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
DIRECTORIES = ["src", "addon", "typings", "tests", "scripts", "runtime", "third_party", ".github"]
ROOT_FILES = ["package.json", "package-lock.json", "tsconfig.json", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "UPSTREAM.json", ".gitignore", ".gitattributes", ".env.example", ".prettierignore", "eslint.config.mjs", "zotero-plugin.config.ts", "updates.json"]
EXCLUDED = {"__pycache__", "node_modules", ".venv", ".build-venv", ".git", ".scaffold", "tmp", "dist"}


def sources():
    for name in ROOT_FILES:
        if (ROOT / name).is_file():
            yield ROOT / name
    for name in DIRECTORIES + ["docs", "bridge/src/zotero_codex_bridge", "bridge/tests"]:
        for path in (ROOT / name).rglob("*"):
            if path.is_file() and not path.is_symlink() and not set(path.relative_to(ROOT).parts) & EXCLUDED and path.suffix not in (".pyc", ".pyo"):
                yield path
    yield ROOT / "bridge/pyproject.toml"
    yield ROOT / "bridge/launcher.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = package["version"]
    if args.public:
        assert re.fullmatch(r"https://github.com/[A-Za-z0-9-]+/zotero-codex-translate/tree/v" + re.escape(version), package.get("homepage", "")), "Configure actual repository first"
        assert subprocess.check_output([*GIT, "rev-parse", "HEAD"], cwd=ROOT).strip() == subprocess.check_output([*GIT, "rev-parse", f"v{version}^{{commit}}"], cwd=ROOT).strip(), "Tag must match HEAD"
        assert not subprocess.check_output([*GIT, "status", "--porcelain"], cwd=ROOT).strip(), "Release source must be clean"
    output = ROOT / "dist" / ("release" if args.public else "local-preview")
    output.mkdir(parents=True, exist_ok=True)
    source = output / f"zotero-codex-translate-{version}-source.zip"
    paths = sorted(set(sources()))
    if args.public:
        tracked = set(subprocess.check_output([*GIT, "ls-files", "-z"], cwd=ROOT).decode("utf-8").rstrip("\0").split("\0"))
        assert tracked == {path.relative_to(ROOT).as_posix() for path in paths}, "Corresponding source must match every tracked file"
    secret = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
    for path in paths:
        if path.suffix not in (".zip", ".png", ".svg"):
            assert not secret.search(path.read_bytes()), f"Potential credential: {path.relative_to(ROOT)}"
        assert path.suffix.lower() not in (".pdf", ".sqlite", ".db", ".exe", ".dll", ".onnx", ".ttf"), f"Forbidden release data: {path}"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            z.write(path, f"zotero-codex-translate-{version}/" + path.relative_to(ROOT).as_posix())
    artifacts = [source]
    import shutil
    xpi = ROOT / "dist" / f"zotero-codex-translate-{version}.xpi"
    shutil.copyfile(xpi, output / xpi.name)
    artifacts.append(output / xpi.name)
    for filename in ["README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "docs/VALIDATION.md", "docs/AUTO_INSTALL.md"]:
        destination = output / Path(filename).name
        shutil.copyfile(ROOT / filename, destination)
        artifacts.append(destination)
    for filename in ["dependency-inventory.json", "source-inventory.json", "wheels-windows-x64.json", "font-provenance.json", "python-downloads.json"]:
        shutil.copyfile(ROOT / "runtime" / filename, output / filename)
        artifacts.append(output / filename)
    shutil.copyfile(ROOT / "bridge/src/zotero_codex_bridge/assets.json", output / "assets.json")
    artifacts.append(output / "assets.json")
    (output / "SHA256SUMS.txt").write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in artifacts), encoding="ascii")
    print(json.dumps({"public": args.public, "files": len(paths), "sourceBytes": source.stat().st_size, "xpiBytes": xpi.stat().st_size, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
