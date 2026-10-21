import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path

from zotero_codex_bridge.errors import BridgeError, Cancelled
from zotero_codex_bridge.storage import Store
from zotero_codex_bridge.translation import TranslationService, TOKENS


class Client:
    def __init__(self, mode="ok"):
        self.calls = []
        self.mode = mode
        self.active = 0
        self.maximum = 0

    def translate_batch(self, rows, model):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.calls.append([r["id"] for r in rows])
        time.sleep(.02)
        self.active -= 1
        if self.mode == "auth":
            raise BridgeError("AUTH_REQUIRED", "login")
        if self.mode == "timeout" and len(self.calls) == 1:
            raise BridgeError("CODEX_TIMEOUT", "timeout", True)
        result = [{"id": r["id"], "text": "完整中文译文 " + " ".join(TOKENS.findall(r["text"]))} for r in reversed(rows)]
        if self.mode == "missing" and len(self.calls) == 1:
            return result[:-1]
        if self.mode == "duplicate" and len(self.calls) == 1:
            return result + [result[-1]]
        if self.mode == "placeholder" and len(self.calls) == 1:
            result[-1]["text"] = "缺少公式的中文译文"
        return result

    def translate(self, text, model):
        self.calls.append([text])
        return "完整中文译文 " + " ".join(TOKENS.findall(text))

    def close(self):
        pass


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.addCleanup(self.store.close)

    def service(self, client):
        service = TranslationService(self.store, lambda: client, "document", "model")
        self.addCleanup(service.close)
        return service

    def run_rows(self, service, count=8):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(service.translate, [f"Scientific paragraph number {i} with formula {{v{i}}}." for i in range(count)]))

    def test_batches_preserve_ids_single_inference_and_cache(self):
        client = Client()
        service = self.service(client)
        result = self.run_rows(service, 24)
        self.assertLessEqual(len(client.calls), 4)
        self.assertEqual(client.maximum, 1)
        for i, text in enumerate(result):
            self.assertIn(f"{{v{i}}}", text)
        previous = len(client.calls)
        self.run_rows(service, 24)
        self.assertEqual(len(client.calls), previous)
        self.assertEqual(service.metrics()["cacheHits"], 24)

    def test_partial_errors_only_retry_bad_ids(self):
        for mode in ("missing", "duplicate", "placeholder"):
            with self.subTest(mode=mode):
                client = Client(mode)
                service = TranslationService(self.store, lambda: client, mode, "model")
                try:
                    self.run_rows(service)
                    self.assertEqual([len(c) for c in client.calls], [8, 1])
                    self.assertEqual(service.metrics()["retries"], 1)
                finally:
                    service.close()

    def test_temporary_timeout_falls_back_to_single_and_succeeds(self):
        client = Client("timeout")
        result = self.run_rows(self.service(client))
        self.assertEqual(len(result), 8)
        self.assertEqual(len(client.calls), 9)

    def test_auth_failure_stops_queue_without_retry(self):
        client = Client("auth")
        service = self.service(client)
        with self.assertRaises(BridgeError) as error:
            self.run_rows(service)
        self.assertEqual(error.exception.code, "AUTH_REQUIRED")
        self.assertEqual(len(client.calls), 1)
        with self.assertRaises(BridgeError):
            service.translate("another paragraph")

    def test_cancel_pending_batch_wakes_waiters(self):
        service = self.service(Client())
        service.cancel.set()
        with self.assertRaises(Cancelled):
            service.translate("An English scientific paragraph")


if __name__ == "__main__":
    unittest.main()
