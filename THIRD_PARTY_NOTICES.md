# Third-party components

New project code: AGPL-3.0-only; the complete license is in LICENSE. Third-party components retain their own licenses. This is not a blanket relicensing of fonts, models, dependencies or upstream code.

## Code bundled in XPI

- **Split-View Reader**, Fuling Lin and contributors, AGPL-3.0-or-later. Repository: https://github.com/zerolfl/zotero-split-view-reader ; pinned commit `600f1bd99d9a52a99683702f6681bf34b83bfec3`. Original license/package grant: `third_party/notices/split-view-*`. Modified files and dates: `third_party/MODIFICATIONS.md`. Corresponding readable TypeScript, styles, assets and bootstrap are included in the matching source release.
- **Zotero Plugin Toolkit 5.1.0-beta.13**, windingwind, MIT. Full notice: `third_party/notices/zotero-plugin-toolkit-LICENSE.txt`. Template and Zotero Make It Red bootstrap original comments remain in source.
- **Material Symbols icons**, Google, Apache-2.0, copied via Split-View Reader. Full license: `third_party/notices/Material-Icons-LICENSE.txt`. The original reader logo retains the upstream project's license.

## PDF engine and downloaded dependencies

**BabelDOC 0.6.4**, awwaawwa and contributors, AGPL version 3. https://github.com/funstory-ai/BabelDOC/tree/v0.6.4 . The bridge uses its internal APIs through one adapter module; the upstream wheel is unmodified.

Verified upstream source sdist SHA256: `dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875`. Matching source is preserved in `third_party/babeldoc-0.6.4-source.zip` (ZIP repack, separate digest alongside), including its nested third-party notices. No claim that BabelDOC grants “or later” is made without an explicit grant.

All 85 Windows wheel files, versions, official PyPI URLs, digests, metadata licenses and copied original notices appear in `runtime/dependency-inventory.json` and `third_party/python-notices/`. Metadata is informational; the original full license text governs. Native libraries may have additional terms in the copied wheel notice bundles. Examples include PyMuPDF/MuPDF (AGPL v3 or commercial; this project uses the AGPL option), Levenshtein (GPL-2.0-or-later; compatible version option applies), numpy/scipy bundled numerical libraries, FreeType and OpenCV. These wheels are downloaded directly from upstream, not included in the XPI. No CUDA optional package is installed.

**uv 0.13.4**, Astral and contributors, MIT OR Apache-2.0. Original texts are in `third_party/notices/uv-*`; source https://github.com/astral-sh/uv/tree/0.13.4 . **CPython 3.12.15**, Python Software Foundation and contributors, PSF license and included component licenses; the upstream standalone archive retains its LICENSE.txt. Build scripts: https://github.com/astral-sh/python-build-standalone/tree/20261013 ; exact archive and SHA256: `runtime/python-downloads.json`. These executables are fetched from the official release, not shipped in XPI.

## Complete BabelDOC resource set

The machine-readable manifest is `bridge/src/zotero_codex_bridge/assets.json`: all files are pinned by SHA3-256, size and immutable upstream revision where available. They are downloaded directly, not republished in our Release.

- Fonts: Go Noto Kurrent, Noto Sans/Serif, Klee One, LXGW WenKai GB/TC, Source Han Sans/Serif, MaruBuri. Each retains **SIL OFL 1.1**; individual copyright/manufacturer/license names are preserved in `runtime/font-provenance.json`. Full OFL and Source reserved-name notice: `third_party/notices/SourceHanSans-OFL.txt`. Go Noto authors explicitly distinguish their Unlicense build scripts from OFL fonts (`go-noto-README.md`). NAVER's official license applies OFL 1.1 to MaruBuri: https://help.naver.com/service/30016/contents/18088?osType=PC&lang=ko ; source evidence retained separately. NAVER/NAVER Cultural Foundation retain copyright. This project neither sells fonts alone nor changes font files/reserved names.
- DocLayout-YOLO-DocStructBench ONNX: the pinned model card declares **Apache-2.0**; card snapshot `third_party/notices/model-README.md`. Model revision is in the manifest. This is a statement about the model weights; it does not relabel the upstream training code's AGPL license.
- Character maps: Adobe CMap data as processed by BabelDOC/pdfminer; retain the Adobe BSD-style and pdfminer MIT notices supplied alongside the corresponding source/evidence. No maps are embedded in XPI.
- o200k_base tiktoken data: OpenAI public encoding data used by MIT-licensed tiktoken; fixed content hash and URL are in the manifest. tiktoken's original notice is in the Python notice inventory.

## External prerequisite

Codex CLI, OpenAI, Apache-2.0: https://github.com/openai/codex . Not bundled or automatically installed. User login and credentials remain managed by Codex itself.

## Corresponding source and release policy

Every published XPI must have an identically versioned source archive, hashes, build scripts, dependency/resource inventory and this notice file. Publish the exact project commit; do not point only at a moving latest branch. Bundled copyleft source is included; direct-download upstream packages retain exact-version provenance. No private documents, credentials, user caches or generated translations belong in a public release. See `docs/RELEASE.md` for the release gate.
