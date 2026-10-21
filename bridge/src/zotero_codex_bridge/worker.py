import contextlib
import json
import sys
import threading
import time
from pathlib import Path

from .codex import CodexClient
from .errors import BridgeError
from .pdf_engine import run_translation
from .storage import Store
from .translation import TranslationService


def run(spec_path):
    wire = sys.stdout

    def emit(value):
        wire.write(json.dumps(value, ensure_ascii=False) + "\n")
        wire.flush()

    # Third party progress bars and diagnostics may never corrupt JSONL stdout.
    with contextlib.redirect_stdout(sys.stderr):
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        store = Store(Path(spec["storeRoot"]), recover=False)
        directory = Path(spec_path).parent
        lock = threading.RLock()
        stopped = threading.Event()
        started = time.monotonic()
        phase_started = started
        phase = "parsing"
        phase_seconds = {}
        last = {"stage": "parsing", "completed": 0, "total": None}
        def metrics():
            with lock:
                timings = dict(phase_seconds)
                timings[phase] = timings.get(phase, 0) + time.monotonic() - phase_started
                # Copy counters without acquiring the service queue lock: its
                # callbacks enter this progress lock in the opposite direction.
                return {**service.stats, "waitingSeconds": round(time.monotonic() - service.waiting_since, 1) if service.waiting_since else 0,
                        "elapsedSeconds": round(time.monotonic() - started, 3), "stageSeconds": {k: round(v, 3) for k, v in timings.items()}}
        def progress(event):
            nonlocal last, phase, phase_started
            with lock:
                current = event.get("stage", phase)
                # Cache hits and batch completion are paragraph statistics;
                # keep upstream's total while its translation stage is active.
                if current != phase:
                    phase_seconds[phase] = phase_seconds.get(phase, 0) + time.monotonic() - phase_started
                    phase, phase_started = current, time.monotonic()
                last = dict(event)
                emit({"event": "progress", **event, "metrics": metrics(), "lastActivityAt": time.time()})
        service = TranslationService(store, lambda: CodexClient(spec.get("codexPath", ""), directory / "sandbox", cancel=stopped), spec["fingerprint"], spec["model"], notify=progress, cache_only=spec.get("cacheOnly", False))
        def heartbeat():
            while not stopped.wait(2):
                with lock:
                    emit({"event": "progress", **last, "metrics": metrics(), "lastActivityAt": time.time(), "heartbeat": True})
        ticker = threading.Thread(target=heartbeat, daemon=True)
        ticker.start()
        try:
            report = run_translation(directory / "input.pdf", directory / "translated.zh-CN.pdf", directory / "work", service,
                                     progress, spec.get("offlineAssets", ""))
            emit({"event": "result", "outputPath": str(directory / "translated.zh-CN.pdf"), **report, "sourceJobId": spec.get("sourceJobId"), "metrics": metrics()})
            return 0
        except BridgeError as e:
            emit({"event": "error", "error": e.as_dict()})
            return 1
        except Exception as e:
            emit({"event": "error", "error": {"code": "WORKER_FAILED", "message": f"{type(e).__name__}: {e}"[:1200], "retryable": False}})
            return 1
        finally:
            stopped.set()
            ticker.join(timeout=3)
            service.close()
            store.close()
