"""The bridge must not reject or rewrite a successful native PDF."""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import pymupdf

from zotero_codex_bridge.errors import BridgeError
from zotero_codex_bridge.pdf_engine import validate_pdf, translate_events


class NativeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from zotero_codex_bridge.pdf_engine import configure_assets
        configure_assets(Path(__file__).resolve().parents[2] / "tmp/test-engine-assets")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "native.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((30, 40), "中文小字", fontname="china-s", fontsize=5)
            page.insert_text((30, 41), "重叠文字", fontname="china-s", fontsize=10)
            document.save(self.path)

    def test_native_small_and_overlapping_text_is_accepted_without_editing(self):
        before = self.path.read_bytes()
        self.assertEqual(validate_pdf(self.path, self.path)["pageCount"], 1)
        self.assertEqual(before, self.path.read_bytes())

    def test_missing_or_corrupt_output_is_rejected(self):
        for name in ("missing.pdf", "bad.pdf"):
            bad = self.path.parent / name
            if name == "bad.pdf": bad.write_bytes(b"bad pdf")
            with self.assertRaises(BridgeError) as error:
                validate_pdf(self.path, bad)
            self.assertEqual(error.exception.code, "INVALID_OUTPUT")

    def test_no_internal_replacement_or_layout_rejection_in_production_adapter(self):
        from zotero_codex_bridge import pdf_engine
        source = Path(pdf_engine.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("unittest.mock", source)
        self.assertNotIn("ILTranslator", source)
        self.assertNotIn("Typesetting", source)
        self.assertNotIn("UNREADABLE_TEXT", source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "CodexTranslator":
                methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
                self.assertEqual(methods, {"__init__", "do_translate", "do_llm_translate", "get_formular_placeholder"})

    def test_upstream_warning_does_not_override_success(self):
        async def events(_config):
            logging.getLogger("babeldoc").warning("预处理段落时出错")
            yield {"type":"finish", "translate_result":SimpleNamespace(mono_pdf_path=self.path)}
        with patch("babeldoc.format.pdf.high_level.async_translate", events):
            result = asyncio.run(translate_events(None, SimpleNamespace(error=None), lambda e:None))
        self.assertEqual(result, self.path)

    def test_engine_error_and_missing_output_are_failures(self):
        for event, expected in [({"type":"error"}, "ENGINE_FAILED"), ({"type":"finish", "translate_result":SimpleNamespace(mono_pdf_path=None)}, "NO_OUTPUT")]:
            async def events(_config): yield event
            with patch("babeldoc.format.pdf.high_level.async_translate", events), self.assertRaises(BridgeError) as error:
                asyncio.run(translate_events(None, SimpleNamespace(error=None), lambda e:None))
            self.assertEqual(error.exception.code, expected)

    def test_real_translator_error_is_not_swallowed_by_upstream_finish(self):
        async def events(_config): yield {"type":"finish", "translate_result":SimpleNamespace(mono_pdf_path=self.path)}
        failure = BridgeError("AUTH_REQUIRED", "login expired")
        with patch("babeldoc.format.pdf.high_level.async_translate", events), self.assertRaises(BridgeError) as error:
            asyncio.run(translate_events(None, SimpleNamespace(error=failure), lambda e:None))
        self.assertIs(error.exception, failure)
