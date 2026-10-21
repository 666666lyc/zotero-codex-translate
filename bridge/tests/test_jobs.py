import io
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from zotero_codex_bridge.errors import BridgeError
from zotero_codex_bridge.server import Server
from zotero_codex_bridge.storage import fingerprint

WORKER = r'''
import json, pathlib, sys, time
spec = pathlib.Path(sys.argv[1])
if (spec.parent / 'slow').exists():
    print(json.dumps({'event':'progress','stage':'parsing','completed':1,'total':2}), flush=True)
    time.sleep(60)
if (spec.parent / 'fail').exists():
    print(json.dumps({'event':'error','error':{'code':'FAKE_FAILURE','message':'interrupted'}}), flush=True)
    sys.exit(1)
output = spec.parent / 'translated.zh-CN.pdf'
output.write_bytes(b'%PDF-test')
print(json.dumps({'event':'result','outputPath':str(output),'pageCount':1,'pageMap':[1]}), flush=True)
'''


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.output = io.StringIO()
        self.server = Server(root / "store", self.output)
        self.addCleanup(self.server.store.close)
        self.addCleanup(self.server.stop_worker)
        self.script = root / "fake_worker.py"
        self.script.write_text(WORKER, encoding="utf-8")
        self.patch = patch("zotero_codex_bridge.server.worker_command", lambda spec: [sys.executable, str(self.script), str(spec)])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def job(self, job_id="abcd"):
        directory = self.server.store.job_dir(job_id)
        directory.mkdir()
        (directory / "input.pdf").write_bytes(b"test input")
        return {"id": job_id, "state": "starting", "createdAt": time.time(), "fingerprint": fingerprint(directory / "input.pdf"), "model": "test"}

    def wait(self):
        self.server.worker_thread.join(timeout=10)
        self.assertFalse(self.server.worker_thread.is_alive())

    def test_failure_resume_and_completed_output(self):
        job = self.job()
        marker = self.server.store.job_dir(job["id"]) / "fail"
        marker.touch()
        self.server.launch(job)
        self.wait()
        self.assertEqual(self.server.store.get_job(job["id"])["state"], "failed")
        marker.unlink()
        self.server.launch(self.server.store.get_job(job["id"]))
        self.wait()
        restored = self.server.store.get_job(job["id"])
        self.assertEqual(restored["state"], "completed")
        self.assertTrue(pathlib.Path(restored["result"]["outputPath"]).is_file())

    def test_cancel_and_single_task_gate(self):
        job = self.job()
        (self.server.store.job_dir(job["id"]) / "slow").touch()
        self.server.launch(job)
        with self.assertRaises(BridgeError) as context:
            self.server.ensure_idle()
        self.assertEqual(context.exception.code, "BUSY")
        self.server.dispatch({"protocolVersion": 1, "id": 2, "method": "jobs.cancel", "params": {"jobId": job["id"]}})
        self.wait()
        self.assertEqual(self.server.store.get_job(job["id"])["state"], "cancelled")

    def test_resume_rejects_modified_snapshot(self):
        job = self.job()
        (self.server.store.job_dir(job["id"]) / "input.pdf").write_bytes(b"changed")
        with self.assertRaises(BridgeError) as context:
            self.server.launch(job)
        self.assertEqual(context.exception.code, "INPUT_CHANGED")

    def test_clean_shutdown_marks_interrupted(self):
        job = self.job()
        (self.server.store.job_dir(job["id"]) / "slow").touch()
        self.server.launch(job)
        self.server.shutdown_requested = True
        self.server.stop_worker()
        self.wait()
        self.assertEqual(self.server.store.get_job(job["id"])["state"], "interrupted")
