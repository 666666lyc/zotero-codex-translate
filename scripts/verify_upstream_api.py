"""Compare a standalone public BabelDOC example and plugin output. No account."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge/src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["reference", "bridge"])
    parser.add_argument("--root", type=Path, default=ROOT / "tmp/upstream-api-015")
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.relative_to((ROOT / "tmp").resolve())
    root.mkdir(parents=True, exist_ok=True)
    if not args.mode:
        import pymupdf
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((55, 65), "Scientific comparison", fontsize=17)
            page.insert_textbox((55, 110, 500, 240), "This scientific experiment evaluates a proposed method. The result improves stability and preserves measured values. Citation [1] identifies the original study.", fontsize=11)
            page.insert_text((90, 300), "x = y + 1", fontsize=15, fontname="tiit")
            page.draw_rect((55, 340, 250, 420), color=(.2, .4, .7))
            page.insert_text((55, 450), "Figure 1. Results of the experiment.", fontsize=10)
            document.save(root / "source.pdf")
        for mode in ["reference", "bridge"]:
            subprocess.run([sys.executable, __file__, "--mode", mode, "--root", str(root), "--assets", str(args.assets.resolve())], check=True)
        with pymupdf.open(root / "reference/result.pdf") as a, pymupdf.open(root / "bridge/result.pdf") as b:
            assert len(a) == len(b)
            pixels = []
            for left, right in zip(a, b):
                lp, rp = left.get_pixmap(), right.get_pixmap()
                assert (lp.width, lp.height) == (rp.width, rp.height)
                assert lp.samples == rp.samples, "Native page render differs"
                pixels.append(hashlib.sha256(lp.samples).hexdigest())
                lp.save(root / "reference-page.png")
                rp.save(root / "bridge-page.png")
        report = {"passed":True,"pages":len(pixels),"renderSha256":pixels,
                  "bridgeMetrics":json.loads((root / "bridge/metrics.json").read_text()),
                  "noInternalReplacement":True}
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        return
    os.environ["ZOTERO_CODEX_ASSET_DIR"] = str(args.assets.resolve())
    # Match the stock Path.home directory resolution in the standalone example.
    real_home = {key:os.environ.get(key) for key in ["HOME","USERPROFILE"]}
    os.environ.update(HOME=str(args.assets.resolve()), USERPROFILE=str(args.assets.resolve()))
    import babeldoc.const
    for key, value in real_home.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value
    from babeldoc.translator.translator import BaseTranslator
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from zotero_codex_bridge.storage import Store, fingerprint
    from zotero_codex_bridge.translation import TranslationService, TOKENS
    original_methods = [ILTranslator.translate, ILTranslator.pre_translate_paragraph, Typesetting.render_page, BaseTranslator.translate]
    class SyntheticClient:
        def translate(self, text, model):
            return "这是科学实验结果的测试译文。" + " ".join(TOKENS.findall(text))
        def close(self): pass
    store = Store(root / "store")
    service = TranslationService(store, SyntheticClient, fingerprint(root / "source.pdf"), "synthetic-public-api", cache_only=args.mode == "bridge")
    output = root / args.mode / "result.pdf"
    output.parent.mkdir(exist_ok=True)
    try:
        if args.mode == "reference":
            from babeldoc.format.pdf.high_level import async_translate
            from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
            from babeldoc.docvision.doclayout import OnnxModel
            class Translator(BaseTranslator):
                name = "zotero-codex"
                def __init__(self):
                    super().__init__("en", "zh", True)
                    self.model = service.model
                def do_translate(self, text, rate_limit_params=None): return service.translate(text)
                def do_llm_translate(self, text, rate_limit_params=None): raise NotImplementedError
                def get_formular_placeholder(self, placeholder_id):
                    return "{v"+str(placeholder_id)+"}", r"\{v"+str(placeholder_id)+r"\}"
            config = TranslationConfig(translator=Translator(), input_file=root / "source.pdf", lang_in="en",lang_out="zh",
                doc_layout_model=OnnxModel.from_pretrained(),output_dir=output.parent,working_dir=output.parent / "work",
                no_dual=True,no_mono=False,qps=1,pool_max_workers=8,use_rich_pbar=False,auto_extract_glossary=False,
                watermark_output_mode=WatermarkOutputMode.NoWatermark,report_interval=.5,skip_scanned_detection=False,
                auto_enable_ocr_workaround=False,min_text_length=1)
            async def run():
                async for event in async_translate(config):
                    if event["type"] == "error": raise RuntimeError("Reference engine failed")
                    if event["type"] == "finish": return event["translate_result"].mono_pdf_path
                raise RuntimeError("Reference did not finish")
            import shutil
            shutil.copyfile(asyncio.run(run()), output)
        else:
            from zotero_codex_bridge.pdf_engine import run_translation
            run_translation(root / "source.pdf", output, output.parent / "work", service, lambda e: None)
            assert service.metrics()["requests"] == 0
        assert original_methods == [ILTranslator.translate, ILTranslator.pre_translate_paragraph, Typesetting.render_page, BaseTranslator.translate]
        (output.parent / "metrics.json").write_text(json.dumps(service.metrics()), encoding="utf-8")
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    main()
