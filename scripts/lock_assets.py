"""Lock every BabelDOC 0.6.4 asset; inspect embedded font provenance."""
import hashlib
import json
import runpy
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "zotero-codex-build"}), timeout=120) as r:
        return r.read()


def main():
    metadata = runpy.run_path(str(ROOT / ".build-venv/Lib/site-packages/babeldoc/assets/embedding_assets_metadata.py"))
    cache = ROOT / "tmp/pdf-pipeline/store/engine-assets/.cache/babeldoc"
    asset_commit = json.loads(get("https://api.github.com/repos/funstory-ai/BabelDOC-Assets/commits/main"))["sha"]
    model_commit = json.loads(get("https://huggingface.co/api/models/wybxc/DocLayout-YOLO-DocStructBench-onnx"))["sha"]
    files = []
    from fontTools.ttLib import TTFont
    fonts = []
    for kind, items in [("fonts", metadata["EMBEDDING_FONT_METADATA"]), ("cmap", metadata["CMAP_METADATA"])]:
        for name, entry in items.items():
            path = cache / kind / name
            data = path.read_bytes()
            digest = entry["sha3_256"] if isinstance(entry, dict) else entry
            assert hashlib.sha3_256(data).hexdigest() == digest, name
            files.append(dict(path=f"{kind}/{name}", url=f"https://raw.githubusercontent.com/funstory-ai/BabelDOC-Assets/{asset_commit}/{kind}/{name}", sha3_256=digest, size=len(data)))
            if kind == "fonts":
                with TTFont(path) as font:
                    records = {str(i): sorted({r.toUnicode() for r in font["name"].names if r.nameID == i}) for i in [0, 8, 13, 14]}
                    fonts.append(dict(file=name, **records))
    for name, digest in metadata["TIKTOKEN_CACHES"].items():
        files.append(dict(path=f"tiktoken/{name}", url="https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken", sha3_256=digest, size=(cache / "tiktoken" / name).stat().st_size))
    name = "doclayout_yolo_docstructbench_imgsz1024.onnx"
    files.append(dict(path=f"models/{name}", url=f"https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/{model_commit}/{name}", sha3_256=metadata["DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256"], size=(cache / "models" / name).stat().st_size))
    manifest = dict(schemaVersion=1, babeldocVersion="0.6.4", assetCommit=asset_commit, modelCommit=model_commit, files=files)
    output = ROOT / "bridge/src/zotero_codex_bridge/assets.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (ROOT / "runtime/font-provenance.json").write_text(json.dumps(fonts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence = ROOT / "third_party/notices"
    evidence.mkdir(parents=True, exist_ok=True)
    sources = {
        "split-view-LICENSE.txt": "https://raw.githubusercontent.com/zerolfl/zotero-split-view-reader/600f1bd99d9a52a99683702f6681bf34b83bfec3/LICENSE",
        "split-view-package.json": "https://raw.githubusercontent.com/zerolfl/zotero-split-view-reader/600f1bd99d9a52a99683702f6681bf34b83bfec3/package.json",
        "go-noto-README.md": "https://raw.githubusercontent.com/satbyy/go-noto-universal/master/README.md",
        "model-README.md": f"https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/raw/{model_commit}/README.md",
        "SourceHanSans-OFL.txt": "https://raw.githubusercontent.com/adobe-fonts/source-han-sans/2.005R/LICENSE.txt",
    }
    for name, url in sources.items():
        (evidence / name).write_bytes(get(url))
    print(len(files), sum(f["size"] for f in files), asset_commit, model_commit)


if __name__ == "__main__":
    main()
