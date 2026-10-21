"""Inventory the *locked installed* wheels and preserve their license notices.

Run only after official-source runtime installation, with --site-packages.
This does not declare that a missing notice is licensed: it fails explicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from email.parser import Parser

ROOT = Path(__file__).resolve().parents[1]


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path, required=True)
    args = parser.parse_args()
    wheels = json.loads((ROOT / "runtime/wheels-windows-x64.json").read_text(encoding="utf-8"))
    packages = {}
    for metadata in args.site_packages.glob("*.dist-info/METADATA"):
        value = Parser().parsestr(metadata.read_text(encoding="utf-8"))
        packages[normalized(value["Name"])] = (metadata.parent, value)
    out = ROOT / "third_party/python-notices"
    out.mkdir(exist_ok=True)
    missing = []
    inventory = []
    for wheel in wheels:
        directory, metadata = packages[normalized(wheel["name"])]
        assert metadata["Version"] == wheel["version"], wheel["name"]
        notices = []
        paths = set()
        for path in directory.rglob("*"):
            if path.is_file() and (any(term in path.name.lower() for term in ["license", "licence", "copying", "notice", "authors", "copyright"]) or "licenses" in path.parts):
                paths.add(path)
        # Include native dependency license bundles outside dist-info.
        for sibling in args.site_packages.iterdir():
            if sibling.is_dir() and normalized(sibling.name.removesuffix(".libs")) == normalized(wheel["name"]):
                for path in sibling.rglob("*"):
                    if path.is_file() and any(term in path.name.lower() for term in ["license", "licence", "copying", "notice"]):
                        paths.add(path)
        for path in sorted(paths):
            relative = path.relative_to(args.site_packages)
            target = out / wheel["name"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            notices.append(dict(path=target.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        if wheel["name"] == "flatbuffers" and not notices:
            path = ROOT / "third_party/notices/flatbuffers-LICENSE.txt"
            notices.append(dict(path=path.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source="https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE"))
        if not notices:
            missing.append(wheel["name"])
        inventory.append(dict(**wheel, classifiers=metadata.get_all("Classifier", []), notices=notices, distribution="downloaded directly from upstream; not bundled in XPI"))
    (ROOT / "runtime/dependency-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Notices:", len(inventory), "Missing:", missing)
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
