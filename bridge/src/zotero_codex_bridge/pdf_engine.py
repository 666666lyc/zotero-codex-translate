"""All BabelDOC 0.6.4 internal API dependencies are confined to this module.

Upstream intentionally tolerates some paragraph failures. We fail closed on
those failures, placeholder loss, and unsuccessful typesetting instead.
"""
import asyncio
import contextlib
import importlib.metadata
import logging
import os
import re
import shutil
from pathlib import Path
from unittest.mock import patch

from . import BABELDOC_VERSION
from .errors import BridgeError


def configure_assets(directory):
    """Import the pinned engine constants with a dedicated local asset home.

    BabelDOC 0.6.4 has no cache-directory environment option and creates its
    tiktoken directory at import time. Scope this override to that one import,
    before workers or other third-party libraries are started.
    """
    directory = os.environ.get("ZOTERO_CODEX_ASSET_DIR", directory)
    with patch.object(Path, "home", return_value=Path(directory).resolve()):
        import babeldoc.const  # noqa: F401


def prepare_assets():
    """Use bounded connections and a realistic timeout for large model files.

    The upstream default launches all downloads with HTTPX's five-second read
    timeout, which is unsuitable for the first-run model/font bundle.
    """
    from .resources import prepare
    from babeldoc.const import CACHE_FOLDER
    prepare(CACHE_FOLDER.parent.parent)


def check_dependencies():
    try:
        version = importlib.metadata.version("babeldoc")
    except importlib.metadata.PackageNotFoundError as e:
        raise BridgeError("DEPENDENCY_MISSING", "桥接程序缺少 BabelDOC，请使用完整桥接包。") from e
    if version != BABELDOC_VERSION:
        raise BridgeError("ENGINE_VERSION", f"需要 BabelDOC {BABELDOC_VERSION}，检测到 {version}。")
    return version


def check_runtime(directory):
    version = check_dependencies()
    configure_assets(directory)
    # Imports exercise bundled native libraries without network or inference.
    from babeldoc.format.pdf.high_level import async_translate  # noqa: F401
    from babeldoc.docvision.doclayout import OnnxModel  # noqa: F401
    return {"ready": True, "babeldocVersion": version}


def inspect_pdf(path):
    import pymupdf
    try:
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise BridgeError("ENCRYPTED_PDF", "不支持需要密码的 PDF。")
            if pdf.page_count == 0:
                raise BridgeError("EMPTY_PDF", "PDF 没有页面。")
            text = [len(p.get_text().strip()) for p in pdf]
            if sum(text) < 40:
                raise BridgeError("SCANNED_PDF", "未提取到足够文本，首版不支持扫描件或无文本层的 PDF。")
            return {"pageCount": pdf.page_count, "textLengths": text}
    except BridgeError:
        raise
    except Exception as e:
        raise BridgeError("INVALID_PDF", "无法读取 PDF，请检查附件是否损坏。") from e


def validate_pdf(source, translated):
    import pymupdf
    with pymupdf.open(source) as original, pymupdf.open(translated) as target:
        if original.page_count != target.page_count:
            raise BridgeError("PAGE_COUNT", "译文页数与原文不一致。")
        for i, (a, b) in enumerate(zip(original, target)):
            if abs(a.rect.width - b.rect.width) > 1 or abs(a.rect.height - b.rect.height) > 1:
                raise BridgeError("PAGE_SIZE", f"第 {i + 1} 页尺寸不一致。")
            if len(a.get_text().strip()) > 80 and len(b.get_text().strip()) < 10:
                raise BridgeError("MISSING_PAGE_TEXT", f"译文第 {i + 1} 页缺少文本。")
            for block in b.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        x0, y0, x1, y1 = span["bbox"]
                        if x0 < -2 or y0 < -2 or x1 > b.rect.width + 2 or y1 > b.rect.height + 2:
                            raise BridgeError("TEXT_CLIPPED", f"译文第 {i + 1} 页有文本超出页面。")
        return {"pageCount": target.page_count, "pageMap": list(range(1, target.page_count + 1))}


class FailureCollector(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.failures = []

    def emit(self, record):
        if record.levelno >= logging.ERROR or any(s in record.getMessage().lower() for s in ("placeholder", "failed to translate", "contentfiltererror", "坐标回绕", "预处理段落时出错")):
            # Avoid retaining source paragraphs in normal diagnostic output.
            self.failures.append(record.name + ": " + record.levelname)


def run_translation(source, output, work_dir, service, notify, offline_assets=""):
    check_dependencies()
    inspect_pdf(source)
    configure_assets(service.store.root / "engine-assets")
    from babeldoc.assets.assets import restore_offline_assets_package
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf.high_level import async_translate
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from babeldoc.translator.translator import BaseTranslator

    notify({"stage": "assets", "completed": 0, "total": None})
    if offline_assets:
        restore_offline_assets_package(Path(offline_assets))
    prepare_assets()  # Upstream verifies embedded SHA3-256 digests for downloaded assets.

    class Translator(BaseTranslator):
        name = "zotero-codex"

        def __init__(self):
            super().__init__("en", "zh", True)
            self.model = service.model

        def do_translate(self, text, rate_limit_params=None):
            return service.translate(text)

        def do_llm_translate(self, text, rate_limit_params=None):
            raise NotImplementedError  # Use paragraph mode for strict per-paragraph validation.

        def get_formular_placeholder(self, placeholder_id):
            return "{v" + str(placeholder_id) + "}", r"\{v" + str(placeholder_id) + r"\}"

    # Preserve the bibliography after the References heading as well as any
    # regions explicitly classified as references by the layout model.
    references = set()
    original_translate = ILTranslator.translate
    original_preprocess = ILTranslator.pre_translate_paragraph
    original_layout = Typesetting._find_optimal_scale_and_layout

    def translate_document(instance, docs):
        after_heading = False
        for page in docs.page:
            for paragraph in page.pdf_paragraph:
                text = (paragraph.unicode or "").strip()
                if re.fullmatch(r"(?:\d+[. ]*)?(?:references|bibliography|literature cited)", text, re.I):
                    after_heading = True
                if after_heading or "reference" in (paragraph.layout_label or "").lower():
                    references.add(id(paragraph))
        return original_translate(instance, docs)

    def preprocess(instance, paragraph, *args, **kwargs):
        if id(paragraph) in references:
            return None, None
        return original_preprocess(instance, paragraph, *args, **kwargs)

    def layout(instance, paragraph, page, units, *args, **kwargs):
        result = original_layout(instance, paragraph, page, units, *args, **kwargs)
        # Recursive fallback may be caught upstream. Record failures separately.
        apply = kwargs.get("apply_layout", args[2] if len(args) > 2 else False)
        if apply and units and (result[1] is None or result[0] < 0.5):
            collector.failures.append("typesetting: incomplete or unreadable paragraph")
            raise BridgeError("LAYOUT_OVERFLOW", "段落无法完整排版，或字体被压缩到不可读大小。")
        return result

    collector = FailureCollector()
    logger = logging.getLogger("babeldoc")
    logger.addHandler(collector)
    config = TranslationConfig(
        translator=Translator(), input_file=Path(source), lang_in="en", lang_out="zh",
        doc_layout_model=OnnxModel.from_pretrained(), output_dir=Path(output).parent,
        working_dir=Path(work_dir), no_dual=True, no_mono=False, qps=1,
        pool_max_workers=1, use_rich_pbar=False, auto_extract_glossary=False,
        watermark_output_mode=WatermarkOutputMode.NoWatermark, report_interval=0.5,
        skip_scanned_detection=False, auto_enable_ocr_workaround=False, min_text_length=1,
        # Table internals and image text remain unchanged; do not enable experimental table rewriting.
    )

    async def execute():
        result = None
        async for event in async_translate(config):
            kind = event.get("type", "")
            if kind.startswith("progress"):
                stage = event.get("stage", "")
                notify({"stage": "translating" if "translat" in stage.lower() else "typesetting" if "typeset" in stage.lower() or "pdf" in stage.lower() and "generat" in stage.lower() else "parsing",
                        "detail": stage, "completed": event.get("stage_current", 0), "total": event.get("stage_total"), "percent": event.get("overall_progress", 0)})
            elif kind == "finish":
                result = event["translate_result"]
                # BabelDOC 0.6.4 leaves its callback iterator open on success.
                # Do not wait for a second event after the terminal result.
                break
            elif kind == "error":
                if service.error:
                    raise service.error
                raise BridgeError("ENGINE_FAILED", str(event.get("error", "PDF 处理失败"))[:1200])
        if service.error:
            raise service.error
        if collector.failures:
            raise BridgeError("INCOMPLETE_DOCUMENT", "检测到翻译或排版失败，未导入不完整译文。可查看诊断并重试。")
        if result is None or not result.mono_pdf_path:
            raise BridgeError("NO_OUTPUT", "排版引擎未生成中文 PDF。")
        return Path(result.mono_pdf_path)

    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(ILTranslator, "translate", translate_document))
            stack.enter_context(patch.object(ILTranslator, "pre_translate_paragraph", preprocess))
            stack.enter_context(patch.object(Typesetting, "_find_optimal_scale_and_layout", layout))
            generated = asyncio.run(execute())
        notify({"stage": "validating", "completed": 0, "total": 1})
        report = validate_pdf(source, generated)
        if generated.resolve() != Path(output).resolve():
            shutil.copyfile(generated, output)
        return report
    finally:
        logger.removeHandler(collector)
