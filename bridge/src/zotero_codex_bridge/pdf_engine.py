"""BabelDOC public API orchestration; no internal method replacement or PDF editing."""
import asyncio
import importlib.metadata
import os
import shutil
import threading
from pathlib import Path

from . import BABELDOC_VERSION, VERSION
from .errors import BridgeError

_ASSET_IMPORT_LOCK = threading.Lock()


def configure_assets(directory):
    """Let unmodified upstream resolve its home in this process's environment.

    Only const.py reads Path.home. Restore HOME/USERPROFILE before Codex or any
    other operation starts, so CLI discovery and credentials retain the real home.
    No Python methods or upstream globals are assigned/replaced.
    """
    directory = Path(os.environ.get("ZOTERO_CODEX_ASSET_DIR", directory)).resolve()
    with _ASSET_IMPORT_LOCK:
        previous = {key: os.environ.get(key) for key in ("HOME", "USERPROFILE")}
        try:
            os.environ.update(HOME=str(directory), USERPROFILE=str(directory))
            from babeldoc.const import CACHE_FOLDER
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if CACHE_FOLDER != directory / ".cache" / "babeldoc":
            raise BridgeError("ASSET_HOME", "PDF 引擎已在另一资源目录初始化，请重启桥接后重试。")


def prepare_assets():
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
    """Accept an openable upstream output; collect page metadata, never reject layout."""
    import pymupdf
    try:
        # A malformed file can retain a Windows native file handle on open failure.
        # Opening the output from bytes avoids that leak and never rewrites it.
        with pymupdf.open(source) as original, pymupdf.open(stream=Path(translated).read_bytes(), filetype="pdf") as target:
            if target.needs_pass or target.page_count == 0:
                raise ValueError("PDF 没有可读取页面")
            matched = original.page_count == target.page_count
            return {"pageCount": target.page_count, "sourcePageCount": original.page_count,
                    "pageMap": list(range(1, target.page_count + 1)) if matched else [],
                    "comparisonAvailable": matched}
    except Exception as error:
        raise BridgeError("INVALID_OUTPUT", "BabelDOC 输出不存在、已损坏或无法打开。") from error


def create_translator(service):
    from babeldoc.translator.translator import BaseTranslator

    class CodexTranslator(BaseTranslator):
        name = "zotero-codex"

        def __init__(self):
            # Existing document/model/prompt cache belongs to TranslationService.
            super().__init__("en", "zh", True)
            self.model = service.model

        def do_translate(self, text, rate_limit_params=None):
            return service.translate(text)

        def do_llm_translate(self, text, rate_limit_params=None):
            # Upstream detects this and uses its standard paragraph translator.
            raise NotImplementedError

        def get_formular_placeholder(self, placeholder_id):
            # Supported translator extension; keep historical cache keys valid.
            return "{v" + str(placeholder_id) + "}", r"\{v" + str(placeholder_id) + r"\}"

    return CodexTranslator()


def translation_config(source, output, work_dir, translator):
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    return TranslationConfig(
        translator=translator, input_file=Path(source), lang_in="en", lang_out="zh",
        doc_layout_model=OnnxModel.from_pretrained(), output_dir=Path(output).parent,
        working_dir=Path(work_dir), no_dual=True, no_mono=False, qps=1,
        pool_max_workers=8, use_rich_pbar=False, auto_extract_glossary=False,
        watermark_output_mode=WatermarkOutputMode.NoWatermark, report_interval=0.5,
        skip_scanned_detection=False, auto_enable_ocr_workaround=False, min_text_length=1,
    )


async def translate_events(config, service, notify):
    from babeldoc.format.pdf.high_level import async_translate
    result = None
    async for event in async_translate(config):
        kind = event.get("type", "")
        if kind.startswith("progress"):
            stage = event.get("stage", "")
            notify({"stage": "translating" if "translat" in stage.lower() else "typesetting" if "typeset" in stage.lower() or "pdf" in stage.lower() and "generat" in stage.lower() else "parsing",
                    "detail": stage, "completed": event.get("stage_current", 0), "total": event.get("stage_total"), "percent": event.get("overall_progress", 0)})
        elif kind == "finish":
            result = event["translate_result"]
            break  # Upstream terminal result; do not wait for another event.
        elif kind == "error":
            if service.error:
                raise service.error
            raise BridgeError("ENGINE_FAILED", "BabelDOC 返回处理失败，请查看任务详情。")
    if service.error:
        raise service.error
    if result is None or not result.mono_pdf_path:
        raise BridgeError("NO_OUTPUT", "BabelDOC 未返回中文 PDF。")
    return Path(result.mono_pdf_path)


def run_translation(source, output, work_dir, service, notify, offline_assets=""):
    check_dependencies()
    inspect_pdf(source)
    configure_assets(service.store.root / "engine-assets")
    notify({"stage": "assets", "completed": 0, "total": None})
    if offline_assets:
        from babeldoc.assets.assets import restore_offline_assets_package
        restore_offline_assets_package(Path(offline_assets))
    prepare_assets()
    config = translation_config(source, output, work_dir, create_translator(service))
    generated = asyncio.run(translate_events(config, service, notify))
    notify({"stage": "validating", "completed": 0, "total": 1})
    report = validate_pdf(source, generated)
    if generated.resolve() != Path(output).resolve():
        shutil.copyfile(generated, output)  # Byte-for-byte copy, never save/render/edit PDF.
    return {**report, "engine": "BabelDOC", "engineVersion": BABELDOC_VERSION,
            "layoutMode": "upstream-api", "simplifiedPages": [], "generatedByVersion": VERSION}
