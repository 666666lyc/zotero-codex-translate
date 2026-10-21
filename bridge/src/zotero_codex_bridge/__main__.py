import argparse
import contextlib
import multiprocessing
import os
import sys
from pathlib import Path


def main():
    multiprocessing.freeze_support()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Zotero Codex PDF bridge (JSONL over stdio)")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--export-assets", type=Path)
    parser.add_argument("--import-assets", type=Path)
    args = parser.parse_args()
    if args.worker:
        from .worker import run
        return run(args.worker)
    if args.export_assets or args.import_assets:
        from .pdf_engine import check_dependencies, configure_assets, prepare_assets
        check_dependencies()
        configure_assets(args.data_dir or (args.export_assets or args.import_assets).resolve().parent / "codex-assets-cache")
        from babeldoc.assets.assets import generate_offline_assets_package, restore_offline_assets_package
        if args.export_assets:
            args.export_assets.mkdir(parents=True, exist_ok=True)
            prepare_assets()
            generate_offline_assets_package(args.export_assets)
        else:
            restore_offline_assets_package(args.import_assets)
        return 0
    if not args.data_dir:
        parser.error("--data-dir is required for stdio mode")
    # stdout exclusively belongs to the protocol, even when dependencies print.
    from .server import Server
    server = Server(args.data_dir, output=sys.stdout)
    with contextlib.redirect_stdout(sys.stderr):
        server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
