import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from zotero_codex_bridge.codex import CodexClient
from zotero_codex_bridge.errors import BridgeError, Cancelled
from zotero_codex_bridge.pdf_engine import inspect_pdf, validate_pdf
from zotero_codex_bridge.server import Server
from zotero_codex_bridge.storage import Store, atomic_json
from zotero_codex_bridge.translation import TranslationService, validate_translation


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.store = Store(self.root / "store")
        self.addCleanup(self.store.close)

    def test_placeholder_multiset_and_untranslated_content(self):
        source = "The result {v1} is <style id='2'>significant</style>."
        validate_translation(source, "结果 {v1} <style id='2'>显著</style>。")
        for value in ["结果显著。", "结果 {v2} <style id='2'>显著</style>。", "", source + " This content remains in English."]:
            with self.assertRaises(BridgeError):
                validate_translation(source, value)

    def test_cache_isolates_document_model_and_prompt(self):
        class FakeClient:
            calls = 0
            def translate(self, text, model):
                self.calls += 1
                return "这是准确的中文译文 {v1}。"
            def close(self):
                pass
        client = FakeClient()
        source = "This paragraph contains a formula {v1}."
        for document, model, expected in [("doc1", "model1", 1), ("doc1", "model1", 1), ("doc2", "model1", 2), ("doc1", "model2", 3)]:
            service = TranslationService(self.store, lambda: client, document, model)
            service.translate(source)
            self.assertEqual(client.calls, expected)

    def test_terminal_failure_stops_subsequent_calls(self):
        class FailedClient:
            calls = 0
            def translate(self, *_):
                self.calls += 1
                raise BridgeError("RATE_LIMIT", "quota")
            def close(self):
                pass
        client = FailedClient()
        service = TranslationService(self.store, lambda: client, "doc", "model")
        for _ in range(2):
            with self.assertRaises(BridgeError):
                service.translate("A complete paragraph of source text.")
        self.assertEqual(client.calls, 1)

    def test_cancellation_does_not_use_cached_or_new_request(self):
        event = threading.Event()
        event.set()
        service = TranslationService(self.store, lambda: self.fail("must not invoke Codex"), "doc", "model", cancel=event)
        with self.assertRaises(Cancelled):
            service.translate("A paragraph")

    def test_recovery_and_worker_store_do_not_corrupt_active_state(self):
        job = {"id": "a-b", "state": "running", "createdAt": 1}
        self.store.save_job(job)
        worker_store = Store(self.store.root, recover=False)
        self.addCleanup(worker_store.close)
        self.assertEqual(worker_store.get_job(job["id"])["state"], "running")
        recovered = Store(self.store.root)
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.get_job(job["id"])["state"], "interrupted")
        with self.assertRaises(ValueError):
            self.store.job_dir("../../escape")

    def test_protocol_errors_and_acknowledgement_idempotence(self):
        output = io.StringIO()
        server = Server(self.root / "server", output)
        self.addCleanup(server.store.close)
        def call(method, params=None, version=1):
            server.dispatch({"id": 1, "protocolVersion": version, "method": method, "params": params or {}})
            return json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(call("hello")["result"]["protocolVersion"], 1)
        self.assertEqual(call("hello", version=9)["error"]["code"], "PROTOCOL_VERSION")
        self.assertEqual(call("unknown")["error"]["code"], "UNKNOWN_METHOD")
        server.store.save_job({"id": "abc", "state": "completed"})
        params = {"jobId": "abc", "outputItemKey": "ABCDEFGH"}
        self.assertEqual(call("jobs.acknowledge", params)["result"]["state"], "imported")
        self.assertEqual(call("jobs.acknowledge", params)["result"]["outputItemKey"], "ABCDEFGH")
        self.assertEqual(call("jobs.acknowledge", {**params, "outputItemKey": "DIFFERENT"})["error"]["code"], "ALREADY_IMPORTED")

    def test_stdio_survives_invalid_json_and_exits_at_eof(self):
        command = [sys.executable, "-m", "zotero_codex_bridge", "--data-dir", str(self.root / "stdio")]
        response = subprocess.run(command, input='broken\n[]\n{"id":1,"protocolVersion":1,"method":"hello"}\n', capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
        messages = [json.loads(line) for line in response.stdout.splitlines()]
        self.assertEqual([m.get("error", {}).get("code") for m in messages[:2]], ["INVALID_JSON", "INVALID_JSON"])
        from zotero_codex_bridge import VERSION
        self.assertEqual(messages[-1]["result"]["version"], VERSION)

    def make_pdf(self, name, pages=1, text="This is a scientific paper containing a sufficiently long English paragraph for text detection and layout validation."):
        import pymupdf
        path = self.root / name
        doc = pymupdf.open()
        for _ in range(pages):
            page = doc.new_page()
            if text:
                page.insert_textbox((40, 40, 500, 300), text)
        doc.save(path)
        doc.close()
        return path

    def test_pdf_inspection_and_page_count_validation(self):
        original = self.make_pdf("英文 文献.pdf", 2)
        self.assertEqual(inspect_pdf(original)["pageCount"], 2)
        self.assertEqual(validate_pdf(original, original)["pageMap"], [1, 2])
        wrong = self.make_pdf("wrong.pdf", 1)
        with self.assertRaises(BridgeError) as context:
            validate_pdf(original, wrong)
        self.assertEqual(context.exception.code, "PAGE_COUNT")
        with self.assertRaises(BridgeError) as context:
            inspect_pdf(self.make_pdf("scan.pdf", text=""))
        self.assertEqual(context.exception.code, "SCANNED_PDF")

    def test_empty_output_page_is_rejected(self):
        with self.assertRaises(BridgeError) as context:
            validate_pdf(self.make_pdf("original.pdf"), self.make_pdf("empty.pdf", text=""))
        self.assertEqual(context.exception.code, "MISSING_PAGE_TEXT")


FAKE_CODEX = r'''
import json, sys
def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    rid = request.get("id")
    if rid is None: continue
    result = {}
    if method == "account/read": result = {"account":{"type":"chatgpt"}}
    if method == "model/list": result = {"data":[{"id":"test","model":"test","isDefault":True}],"nextCursor":None}
    if method == "thread/start": result = {"thread":{"id":"thread1"}}
    if method == "turn/start":
        # Notifications intentionally precede the RPC reply to exercise buffering.
        emit({"method":"item/completed","params":{"threadId":"thread1","item":{"type":"agentMessage","phase":"final_answer","text":json.dumps({"text":"这是中文译文 {v1}。"},ensure_ascii=False)}}})
        emit({"method":"turn/completed","params":{"threadId":"thread1","turn":{"status":"completed"}}})
        result = {"turn":{"id":"turn1","status":"inProgress"}}
    emit({"id":rid,"result":result})
'''


class CodexTransportTests(unittest.TestCase):
    def test_transport_model_catalog_and_early_notifications(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            script = root / "fake_codex.py"
            script.write_text(FAKE_CODEX, encoding="utf-8")
            real_popen = subprocess.Popen
            def fake_popen(command, **kwargs):
                return real_popen([sys.executable, str(script)], **kwargs)
            with patch("zotero_codex_bridge.codex.subprocess.Popen", fake_popen):
                with CodexClient(sys.executable, root / "sandbox") as client:
                    self.assertTrue(client.inspect()["models"][0]["isDefault"])
                    self.assertEqual(client.translate("A formula {v1}.", "test"), "这是中文译文 {v1}。")

    def test_error_classification(self):
        self.assertEqual(CodexClient.classify("HTTP 401 unauthorized").code, "AUTH_REQUIRED")
        self.assertEqual(CodexClient.classify("rate limit reached").code, "RATE_LIMIT")
        self.assertTrue(CodexClient.classify("503 connection lost").retryable)


if __name__ == "__main__":
    unittest.main()
