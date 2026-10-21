import contextlib
import json
import sys
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
        service = TranslationService(store, lambda: CodexClient(spec.get("codexPath", ""), directory / "sandbox"), spec["fingerprint"], spec["model"], notify=lambda event: emit({"event": "progress", **event}))
        try:
            report = run_translation(directory / "input.pdf", directory / "translated.zh-CN.pdf", directory / "work", service,
                                     lambda event: emit({"event": "progress", **event}), spec.get("offlineAssets", ""))
            emit({"event": "result", "outputPath": str(directory / "translated.zh-CN.pdf"), **report})
            return 0
        except BridgeError as e:
            emit({"event": "error", "error": e.as_dict()})
            return 1
        except Exception as e:
            emit({"event": "error", "error": {"code": "WORKER_FAILED", "message": f"{type(e).__name__}: {e}"[:1200], "retryable": False}})
            return 1
        finally:
            service.close()
            store.close()
