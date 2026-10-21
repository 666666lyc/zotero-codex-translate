"""Record immutable source-distribution URLs for the exact locked packages."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def source(wheel):
    url = f"https://pypi.org/pypi/{wheel['name']}/{wheel['version']}/json"
    with urllib.request.urlopen(url, timeout=90) as response:
        metadata = json.load(response)
    sources = [dict(filename=f["filename"], url=f["url"], sha256=f["digests"]["sha256"], size=f["size"]) for f in metadata["urls"] if f["packagetype"] == "sdist"]
    record = dict(name=wheel["name"], version=wheel["version"], metadataURL=url, sourceDistributions=sources, projectURLs=metadata["info"].get("project_urls"))
    if not sources and wheel["name"] in ("flatbuffers", "onnxruntime"):
        repository = {"flatbuffers": "google/flatbuffers", "onnxruntime": "microsoft/onnxruntime"}[wheel["name"]]
        with urllib.request.urlopen(f"https://api.github.com/repos/{repository}/git/ref/tags/v{wheel['version']}", timeout=60) as response:
            ref = json.load(response)["object"]
        if ref["type"] == "tag":
            with urllib.request.urlopen(ref["url"], timeout=60) as response:
                ref = json.load(response)["object"]
        record.update(upstreamSourceCommit=ref["sha"], upstreamSourceArchive=f"https://github.com/{repository}/archive/{ref['sha']}.tar.gz")
    return record


if __name__ == "__main__":
    wheels = json.loads((ROOT / "runtime/wheels-windows-x64.json").read_text(encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(source, wheels))
    (ROOT / "runtime/source-inventory.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print("Source records:", len(records), "No sdist:", [r["name"] for r in records if not r["sourceDistributions"]])
