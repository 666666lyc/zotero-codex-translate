"""Opt-in real Codex comparison: cold paragraph caches, fixed model/text.

Uses synthetic text only, never private documents. Costs account quota.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge/src"))
from zotero_codex_bridge.codex import CodexClient
from zotero_codex_bridge.storage import Store
from zotero_codex_bridge.translation import TranslationService, validate_translation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--paragraphs", type=int, default=8)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/benchmark-013")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"model": args.model, "rounds": [], "passed": False, "syntheticOnly": True}
    texts = [f"Experiment {i + 1} evaluates a robot on uneven terrain. The measured parameter is {{v{i}}}. We report the numerical results and compare the proposed method with a baseline." for i in range(args.paragraphs)]
    try:
        for round_index in range(args.rounds):
            row = {"round": round_index + 1}
            for mode in ("single", "batch"):
                root = args.output / f"round-{round_index + 1}-{mode}"
                root.mkdir(parents=True, exist_ok=True)
                # Fresh DB name makes reruns cold without deleting prior data.
                store = Store(root / str(time.time_ns()))
                client = CodexClient("", root / "sandbox")
                client._effort(args.model)
                service = TranslationService(store, lambda: client, "benchmark", args.model)
                start = time.monotonic()
                try:
                    if mode == "single":
                        values = [validate_translation(text, client.translate(text, args.model)) for text in texts]
                        metrics = {"requests": len(values), "retries": 0, "cacheHits": 0}
                    else:
                        with ThreadPoolExecutor(max_workers=8) as pool:
                            values = list(pool.map(service.translate, texts))
                        metrics = service.metrics()
                    row[mode] = {"seconds": round(time.monotonic() - start, 3), **metrics}
                    assert all(f"{{v{i}}}" in value for i, value in enumerate(values))
                    print(json.dumps({"round": round_index + 1, "mode": mode, **row[mode]}), flush=True)
                finally:
                    service.close(); client.close(); store.close()
            report["rounds"].append(row)
            (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        before = statistics.median(r["single"]["seconds"] for r in report["rounds"])
        after = statistics.median(r["batch"]["seconds"] for r in report["rounds"])
        requests_before = sum(r["single"]["requests"] for r in report["rounds"])
        requests_after = sum(r["batch"]["requests"] for r in report["rounds"])
        report.update(singleMedianSeconds=before, batchMedianSeconds=after,
                      timeReduction=1-after/before, requestReduction=1-requests_after/requests_before)
        report["passed"] = report["timeReduction"] >= .25 and report["requestReduction"] >= .6
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)[:1000]}
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
