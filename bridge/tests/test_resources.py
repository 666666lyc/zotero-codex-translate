import hashlib
import io
import tempfile
import threading
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from zotero_codex_bridge.errors import BridgeError, Cancelled
from zotero_codex_bridge.resources import prepare, safe_path


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = b"verified fixture"
        self.entry = dict(path="fonts/example.ttf", size=len(self.payload), sha3_256=hashlib.sha3_256(self.payload).hexdigest(), url="https://raw.githubusercontent.com/example/pin/example.ttf")

    def tearDown(self):
        self.temporary.cleanup()

    def test_traversal_absolute_and_symlinks_rejected(self):
        for name in ["../escape", "/escape", "C:/escape", "x\\escape", "x/../../escape"]:
            with self.assertRaises(ValueError):
                safe_path(self.root, name)

    def test_verified_cache_does_not_download_and_corruption_is_detected(self):
        path = self.root / ".cache/babeldoc/fonts/example.ttf"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        with patch("zotero_codex_bridge.resources.manifest", return_value={"files": [self.entry]}), patch("urllib.request.build_opener") as opener:
            self.assertEqual(prepare(self.root, verify_only=True)["downloadedBytes"], 0)
            opener.return_value.open.assert_not_called()
            path.write_bytes(b"corrupt")
            with self.assertRaises(BridgeError):
                prepare(self.root, verify_only=True)

    def test_download_hash_failure_never_marks_ready_or_replaces_good_file(self):
        opener = type("Opener", (), {"open": lambda *args, **kwargs: io.BytesIO(b"wrong")})()
        with patch("zotero_codex_bridge.resources.manifest", return_value={"files": [self.entry]}), patch("urllib.request.build_opener", return_value=opener), patch("threading.Event.wait", return_value=False):
            with self.assertRaisesRegex(BridgeError, "资源摘要不符"):
                prepare(self.root)
        self.assertFalse((self.root / ".cache/babeldoc/fonts/example.ttf").exists())

    def test_cancel_preserves_verified_files_and_can_continue(self):
        cancel = threading.Event()
        cancel.set()
        with patch("zotero_codex_bridge.resources.manifest", return_value={"files": [self.entry]}):
            with self.assertRaises(Cancelled):
                prepare(self.root, cancelled=cancel)
        opener = type("Opener", (), {"open": lambda *args, **kwargs: io.BytesIO(self.payload)})()
        with patch("zotero_codex_bridge.resources.manifest", return_value={"files": [self.entry]}), patch("urllib.request.build_opener", return_value=opener):
            self.assertTrue(prepare(self.root)["ready"])
            self.assertEqual(prepare(self.root)["downloadedBytes"], 0)

    def test_protocol_preparation_is_nonblocking_and_cancellable(self):
        from zotero_codex_bridge.server import Server
        output = io.StringIO()
        server = Server(self.root / "server", output=output)
        entered = threading.Event()
        def fake_prepare(directory, notify, cancel):
            entered.set()
            cancel.wait(5)
            raise Cancelled()
        try:
            with patch("zotero_codex_bridge.resources.prepare", side_effect=fake_prepare):
                server.dispatch(dict(protocolVersion=1, id=1, method="hello"))
                server.dispatch(dict(protocolVersion=1, id=2, method="resources.prepare"))
                self.assertTrue(entered.wait(2))
                server.dispatch(dict(protocolVersion=1, id=3, method="jobs.start", params={}))
                server.dispatch(dict(protocolVersion=1, id=4, method="resources.cancel"))
                server.resource_thread.join(3)
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertIn("resources.prepare", messages[0]["result"]["capabilities"])
            self.assertTrue(next(m for m in messages if m.get("id") == 2)["result"]["started"])
            self.assertEqual(next(m for m in messages if m.get("id") == 3)["error"]["code"], "BUSY")
            self.assertTrue(any(m.get("event") == "resources.failed" and m["error"]["code"] == "CANCELLED" for m in messages))
        finally:
            server.resource_cancel.set()
            if server.resource_thread:
                server.resource_thread.join(5)
            server.store.close()


if __name__ == "__main__":
    unittest.main()
