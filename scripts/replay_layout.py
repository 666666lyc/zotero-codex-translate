"""Replay a private failed input/cache into tmp, with zero model requests."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge/src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/layout-replay")
    parser.add_argument("--resources", type=Path, default=ROOT / "tmp/zotero-smoke-c2b84740/data/codex-translate/engine-assets")
    parser.add_argument("--protocol", action="store_true")
    parser.add_argument("--relayout", action="store_true", help="Exercise cache-only new-version protocol")
    args = parser.parse_args()
    out = args.output.resolve()
    out.relative_to((ROOT / "tmp").resolve())
    out.mkdir(parents=True, exist_ok=True)
    source_dir = args.root / "jobs" / args.job
    job = json.loads((source_dir / "job.json").read_text(encoding="utf-8"))
    shutil.copyfile(source_dir / "input.pdf", out / "input.pdf")
    # SQLite backup reads WAL consistently, never opens the user's DB writable.
    with sqlite3.connect((args.root / "cache.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(out / "cache.sqlite3") as target:
            source.backup(target)
    os.environ["ZOTERO_CODEX_ASSET_DIR"] = str(args.resources.resolve())
    if args.protocol or args.relayout:
        from zotero_codex_bridge.storage import Store
        job_dir = out / "jobs" / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out / "input.pdf", job_dir / "input.pdf")
        job.update(storeRoot=str(out), codexPath=str(out / "must-not-call-codex.exe"))
        with sqlite3.connect(out / "cache.sqlite3"):
            pass
        store = Store(out)
        store.save_job(job)
        store.close()
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "bridge/src"))
        with (out / "worker-stderr.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "zotero_codex_bridge", "--data-dir", str(out)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", env=environment)
            process.stdin.write(json.dumps({"protocolVersion":1,"id":1,"method":"jobs.relayout" if args.relayout else "jobs.resume","params":{"jobId":job["id"], "sourcePath": str(out / "input.pdf")}})+"\n")
            process.stdin.flush()
            report = {"jobId":job["id"],"model":job["model"],"passed":False,"protocol":True}
            try:
                for line in process.stdout:
                    event = json.loads(line)
                    if event.get("id") == 1 and event.get("error"):
                        report["error"] = event["error"]; break
                    if event.get("event") == "job.changed":
                        report["job"] = event["job"]
                        report["passed"] = event["job"]["state"] == "completed" and event["job"].get("metrics",{}).get("requests") == 0
                        break
            finally:
                process.stdin.close()
                process.wait(timeout=30)
                process.stdout.close()
            (out / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(report,ensure_ascii=True),flush=True)
        return
    from zotero_codex_bridge.storage import Store
    from zotero_codex_bridge.translation import TranslationService
    from zotero_codex_bridge.pdf_engine import run_translation
    store = Store(out)
    def no_model():
        raise AssertionError("CACHE_MISS: replay must not call a model")
    service = TranslationService(store, no_model, job["fingerprint"], job["model"])
    report = {"jobId": job["id"], "model": job["model"], "passed": False}
    start = time.monotonic()
    try:
        report["result"] = run_translation(out / "input.pdf", out / "translated.pdf", out / "work", service, lambda e: None)
        report["passed"] = True
    except Exception as error:
        import traceback
        traceback.print_exc()
        report["error"] = error.as_dict() if hasattr(error, "as_dict") else {"type": type(error).__name__, "message": str(error)}
    finally:
        report["seconds"] = round(time.monotonic() - start, 3)
        if hasattr(service, "metrics"):
            report["metrics"] = service.metrics()
        service.close()
        store.close()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
