"""Opt-in real-account test of a packaged bridge using a synthetic PDF.

Consumes Codex account quota. Never reads a personal Zotero library or tokens.
Run after login; all test data and diagnostics stay under project tmp/.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--bridge", type=Path, required=True)
parser.add_argument("--managed", action="store_true", help="Bridge is an automatically installed Python executable")
parser.add_argument("--resource-dir", type=Path)
parser.add_argument("--source", type=Path, default=Path("tmp/pdf-pipeline/fixture.pdf"))
parser.add_argument("--offline-assets", type=Path)
parser.add_argument("--timeout", type=int, default=1200)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
directory = root / "tmp" / ("live-codex-e2e-" + uuid.uuid4().hex[:8])
directory.mkdir(parents=True)
report = {"passed": False, "checks": [], "source": str(args.source.resolve())}
inbox = queue.Queue()
events = []
with (directory / "bridge.stderr.log").open("w", encoding="utf-8") as log:
    environment = os.environ.copy()
    if args.managed:
        environment["ZOTERO_CODEX_MANAGED"] = "1"
    if args.resource_dir:
        environment["ZOTERO_CODEX_ASSET_DIR"] = str(args.resource_dir.resolve())
    process = subprocess.Popen([str(args.bridge.resolve()), *(["-I", "-m", "zotero_codex_bridge"] if args.managed else []), "--data-dir", str(directory / "data")], env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    def read():
        try:
            for line in process.stdout:
                inbox.put(json.loads(line))
        finally:
            inbox.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    serial = 0
    def request(method, params=None, timeout=180):
        global serial
        serial += 1
        process.stdin.write(json.dumps({"protocolVersion": 1, "id": serial, "method": method, "params": params or {}}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = inbox.get(timeout=max(0.1, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError("Bridge exited")
            if message.get("id") == serial:
                if "error" in message:
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
                return message["result"]
            events.append(message)
        raise TimeoutError(method)
    try:
        environment = request("diagnose")
        assert environment["authenticated"] and environment["engineReady"]
        default = next(m for m in environment["models"] if m.get("isDefault"))
        report.update(model=default.get("model") or default["id"], engine=environment["babeldocVersion"])
        report["checks"].append("chatgpt-login-and-runtime-default-model")
        print("Authenticated; default model:", report["model"], flush=True)
        params = {"sourcePath": str(args.source.resolve()), "sourceItemKey": "SYNTH001", "parentItemKey": "PARENT01", "libraryID": 1}
        if args.offline_assets:
            params["offlineAssets"] = str(args.offline_assets.resolve())
        job = request("jobs.start", params)
        report["jobId"] = job["id"]
        deadline = time.monotonic() + args.timeout
        last_stage = None
        while time.monotonic() < deadline:
            message = events.pop(0) if events else inbox.get(timeout=max(0.1, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError("Bridge exited during translation")
            if message.get("jobId") != job["id"]:
                continue
            if message.get("event") == "job.progress":
                stage = message.get("stage")
                if stage != last_stage:
                    print("Stage:", stage, flush=True)
                    last_stage = stage
            elif message.get("event") == "job.changed":
                job = message["job"]
                break
        if job["state"] != "completed":
            raise RuntimeError(json.dumps(job.get("error") or {"state": job["state"]}, ensure_ascii=False))
        report["checks"].append("packaged-worker-real-codex-translation")
        import pymupdf
        output = Path(job["result"]["outputPath"])
        with pymupdf.open(args.source) as source, pymupdf.open(output) as pdf:
            assert pdf.page_count == source.page_count == 2
            first_text = pdf[0].get_text()
            assert sum("\u4e00" <= c <= "\u9fff" for c in first_text) >= 20
            assert "Smith J." in pdf[1].get_text()
            assert "{v" not in first_text
            for index, page in enumerate(pdf):
                page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(directory / f"page-{index+1}.png")
        report["checks"].extend(["chinese-text-and-page-correspondence", "bibliography-retained"])
        report.update(passed=True, outputPath=str(output), pageCount=2)
    except Exception as error:
        report["error"] = str(error)
    finally:
        try:
            request("shutdown", timeout=30)
        except Exception:
            pass
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=5)
        process.stdout.close()
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
        print("Report:", directory / "report.json", flush=True)
raise SystemExit(0 if report["passed"] else 1)
