import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import BABELDOC_VERSION, PROTOCOL_VERSION, VERSION
from .codex import CodexClient, resolve_codex
from .errors import BridgeError
from .storage import Store, atomic_json, fingerprint


def worker_command(spec):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", str(spec)]
    isolated = ["-I"] if os.environ.get("ZOTERO_CODEX_MANAGED") == "1" else []
    return [sys.executable, *isolated, "-m", "zotero_codex_bridge", "--worker", str(spec)]


class Server:
    def __init__(self, root, output=None):
        self.store = Store(Path(root))
        self.output = output or sys.stdout
        self.write_lock = threading.Lock()
        self.worker = None
        self.worker_thread = None
        self.active_job = None
        self.cancelled = False
        self.shutdown_requested = False
        self.resource_cancel = threading.Event()
        self.resource_thread = None

    def send(self, message):
        with self.write_lock:
            self.output.write(json.dumps({"protocolVersion": PROTOCOL_VERSION, **message}, ensure_ascii=False) + "\n")
            self.output.flush()

    def dispatch(self, message):
        request_id = message.get("id")
        try:
            if message.get("protocolVersion") != PROTOCOL_VERSION:
                raise BridgeError("PROTOCOL_VERSION", "插件和桥接程序版本不兼容，请配套升级。")
            params = message.get("params", {})
            method = message.get("method")
            if method == "hello":
                result = {"version": VERSION, "protocolVersion": PROTOCOL_VERSION, "babeldocVersion": BABELDOC_VERSION, "capabilities": ["resources.prepare", "resources.cancel"]}
            elif method == "resources.prepare":
                self.ensure_idle()
                if self.resource_thread and self.resource_thread.is_alive():
                    raise BridgeError("BUSY", "正在准备资源。")
                self.resource_cancel.clear()
                self.resource_thread = threading.Thread(target=self.prepare_resources, daemon=True)
                self.resource_thread.start()
                result = {"started": True}
            elif method == "resources.cancel":
                self.resource_cancel.set()
                result = {"cancelled": True}
            elif method == "diagnose":
                try:
                    installed = importlib.metadata.version("babeldoc")
                except importlib.metadata.PackageNotFoundError:
                    installed = None
                with CodexClient(params.get("codexPath", ""), self.store.root / "diagnostics") as client:
                    result = {"babeldocVersion": installed, "engineReady": installed == BABELDOC_VERSION, "codexPath": resolve_codex(params.get("codexPath", "")), **client.inspect()}
            elif method == "jobs.list":
                result = self.store.all_jobs()
            elif method == "document.inspect":
                from .pdf_engine import inspect_pdf
                source = Path(params["sourcePath"]).resolve(strict=True)
                result = {"fingerprint": fingerprint(source), **inspect_pdf(source)}
            elif method == "engine.check":
                from .pdf_engine import check_runtime
                result = check_runtime(self.store.root / "engine-assets")
            elif method == "jobs.status":
                result = self.store.get_job(params["jobId"])
            elif method == "jobs.start":
                result = self.start(params)
            elif method == "jobs.resume":
                job = self.store.get_job(params["jobId"])
                if job["state"] in ("completed", "imported"):
                    result = job
                else:
                    result = self.launch(job)
            elif method == "jobs.cancel":
                if not self.active_job or self.active_job != params["jobId"]:
                    raise BridgeError("NO_ACTIVE_JOB", "此任务当前未运行。")
                self.cancelled = True
                self.stop_worker()
                result = {"cancelled": True}
            elif method == "jobs.acknowledge":
                job = self.store.get_job(params["jobId"])
                if job["state"] not in ("completed", "imported"):
                    raise BridgeError("NOT_COMPLETED", "任务尚未完成，不能确认导入。")
                existing = job.get("outputItemKey")
                if existing and existing != params["outputItemKey"]:
                    raise BridgeError("ALREADY_IMPORTED", "此任务已经导入到另一个附件。")
                job.update(state="imported", outputItemKey=params["outputItemKey"])
                self.store.save_job(job)
                result = job
            elif method == "shutdown":
                self.shutdown_requested = True
                self.stop_worker()
                result = {"stopped": True}
            else:
                raise BridgeError("UNKNOWN_METHOD", "未知协议方法。")
            self.send({"id": request_id, "result": result})
        except BridgeError as e:
            self.send({"id": request_id, "error": e.as_dict()})
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.send({"id": request_id, "error": {"code": "BAD_REQUEST", "message": str(e)[:1000], "retryable": False}})
        except Exception as e:
            self.send({"id": request_id, "error": {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"[:1000], "retryable": False}})

    def ensure_idle(self):
        if self.resource_thread and self.resource_thread.is_alive():
            raise BridgeError("BUSY", "正在准备资源。")
        if self.worker_thread and self.worker_thread.is_alive():
            raise BridgeError("BUSY", "已有文献正在处理，请完成或取消后再开始。")

    def prepare_resources(self):
        from .resources import prepare
        try:
            directory = os.environ.get("ZOTERO_CODEX_ASSET_DIR", self.store.root / "engine-assets")
            result = prepare(directory, lambda event: self.send({"event": "resources.progress", **event}), self.resource_cancel)
            self.send({"event": "resources.completed", **result})
        except BridgeError as e:
            self.send({"event": "resources.failed", "error": e.as_dict()})

    def start(self, params):
        self.ensure_idle()
        source = Path(params["sourcePath"]).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise BridgeError("INVALID_INPUT", "请选择本地 PDF 附件。")
        if not params.get("parentItemKey") or not params.get("sourceItemKey"):
            raise BridgeError("PARENT_REQUIRED", "PDF 必须归属于文献条目。")
        model = params.get("model", "")
        with CodexClient(params.get("codexPath", ""), self.store.root / "diagnostics") as client:
            models = client.inspect()["models"]
            if not model:
                default = next((m for m in models if m.get("isDefault")), None)
                if default is None:
                    raise BridgeError("MODEL_REQUIRED", "Codex 未返回默认模型，请在设置中选择模型。")
                model = default.get("model") or default["id"]
            if not any(model in (m.get("model"), m.get("id")) for m in models):
                raise BridgeError("MODEL_UNAVAILABLE", "所选模型不在 Codex 模型列表中。")
        job_id = str(uuid.uuid4())
        directory = self.store.job_dir(job_id)
        directory.mkdir()
        try:
            shutil.copyfile(source, directory / "input.pdf")
            job = {"id": job_id, "createdAt": time.time(), "state": "starting", "sourceItemKey": params["sourceItemKey"],
                   "parentItemKey": params["parentItemKey"], "libraryID": params["libraryID"], "model": model,
                   "fingerprint": fingerprint(directory / "input.pdf"), "codexPath": params.get("codexPath", ""),
                   "offlineAssets": params.get("offlineAssets", ""), "storeRoot": str(self.store.root), "progress": {}}
            self.store.save_job(job)
            return self.launch(job)
        except Exception:
            # Keep any persisted job for diagnostics; only a copied input exists otherwise.
            raise

    def launch(self, job):
        self.ensure_idle()
        directory = self.store.job_dir(job["id"])
        if fingerprint(directory / "input.pdf") != job["fingerprint"]:
            raise BridgeError("INPUT_CHANGED", "缓存中的原文已发生变化，不能继续该任务。")
        spec = directory / "spec.json"
        atomic_json(spec, job)
        job.update(state="running", error=None)
        self.store.save_job(job)
        self.cancelled = False
        self.active_job = job["id"]
        self.worker = subprocess.Popen(worker_command(spec), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.worker_thread = threading.Thread(target=self.monitor, args=(job, self.worker), daemon=True)
        self.worker_thread.start()
        return job.copy()

    def monitor(self, job, process):
        result = None
        error = None
        last_saved = 0
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "progress":
                job["progress"] = event
                if time.monotonic() - last_saved > 1:
                    self.store.save_job(job)
                    last_saved = time.monotonic()
                self.send({"event": "job.progress", "jobId": job["id"], **{k: v for k, v in event.items() if k != "event"}})
            elif event.get("event") == "result":
                result = event
            elif event.get("event") == "error":
                error = event["error"]
        exit_code = process.wait()
        process.stdout.close()
        if self.cancelled:
            job.update(state="cancelled", error=None)
        elif self.shutdown_requested:
            job.update(state="interrupted", error=None)
        elif exit_code == 0 and result and not error:
            expected = self.store.job_dir(job["id"]) / "translated.zh-CN.pdf"
            if Path(result["outputPath"]).resolve() == expected.resolve() and expected.is_file():
                job.update(state="completed", result=result)
            else:
                job.update(state="failed", error={"code": "INVALID_RESULT", "message": "结果路径无效。"})
        else:
            job.update(state="failed", error=error or {"code": "WORKER_CRASH", "message": "处理进程意外退出，可以续跑。", "retryable": True})
        self.store.save_job(job)
        self.active_job = None
        self.send({"event": "job.changed", "jobId": job["id"], "job": job})

    def stop_worker(self):
        process = self.worker
        if not process or process.poll() is not None:
            return
        # psutil is a pinned engine dependency. Terminate Codex descendants too.
        import psutil
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
            _, alive = psutil.wait_procs(children + [parent], timeout=3)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        if self.worker_thread and self.worker_thread is not threading.current_thread():
            self.worker_thread.join(timeout=5)

    def run(self, input_stream=None):
        try:
            for line in input_stream or sys.stdin:
                if len(line) > 1024 * 1024:
                    self.send({"id": None, "error": {"code": "MESSAGE_TOO_LARGE", "message": "请求过大。"}})
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("Expected an object")
                    self.dispatch(message)
                except ValueError:
                    self.send({"id": None, "error": {"code": "INVALID_JSON", "message": "无效 JSON 请求。"}})
                if self.shutdown_requested:
                    break
        finally:
            self.shutdown_requested = True
            self.resource_cancel.set()
            if self.resource_thread:
                self.resource_thread.join(timeout=35)
            self.stop_worker()
            self.store.close()
