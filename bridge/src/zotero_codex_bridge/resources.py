"""Pinned, first-party-only asset downloads. No fastest-mirror discovery."""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from .errors import BridgeError, Cancelled


def manifest():
    return json.loads(Path(__file__).with_name("assets.json").read_text(encoding="utf-8"))


def safe_path(root, relative):
    name = PurePosixPath(relative)
    if name.is_absolute() or ".." in name.parts or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe resource path")
    root = Path(root).resolve()
    target = root.joinpath(*name.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError("Resource path leaves managed directory")
    # A local symlink must never redirect downloads outside the managed root.
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError("Resource symlinks are not accepted")
    return target


def valid(path, entry):
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha3_256").hexdigest() == entry["sha3_256"]


def prepare(directory, notify=lambda event: None, cancelled=None, verify_only=False):
    cancelled = cancelled or threading.Event()
    root = Path(directory) / ".cache" / "babeldoc"
    root.mkdir(parents=True, exist_ok=True)
    files = manifest()["files"]
    total = sum(e["size"] for e in files)
    completed = 0
    downloaded = 0
    # Use OS certificate trust, never local pip/uv mirrors or credential files.
    opener = urllib.request.build_opener()
    for entry in files:
        if cancelled.is_set():
            raise Cancelled()
        path = safe_path(root, entry["path"])
        if not valid(path, entry):
            if verify_only:
                raise BridgeError("RESOURCE_MISSING", f"资源缺失或摘要不符：{entry['path']}")
            host = urllib.parse.urlsplit(entry["url"]).hostname
            if not entry["url"].startswith("https://"):
                raise BridgeError("RESOURCE_SOURCE", "资源必须来自 HTTPS。")
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".part")
            for attempt in range(3):
                try:
                    done = 0
                    last = 0
                    with opener.open(urllib.request.Request(entry["url"], headers={"User-Agent": "zotero-codex-translate/0.2"}), timeout=30) as response, partial.open("wb") as output:
                        while chunk := response.read(256 * 1024):
                            if cancelled.is_set():
                                raise Cancelled()
                            output.write(chunk)
                            done += len(chunk)
                            downloaded += len(chunk)
                            if done > entry["size"]:
                                raise BridgeError("RESOURCE_SIZE", f"资源长度超出锁定值：{entry['path']}")
                            if time.monotonic() - last > .25:
                                notify(dict(stage="resources", file=entry["path"], host=host, completed=completed + done, total=total))
                                last = time.monotonic()
                    if not valid(partial, entry):
                        raise BridgeError("RESOURCE_HASH", f"资源摘要不符：{host}/{entry['path']}")
                    os.replace(partial, path)
                    break
                except Cancelled:
                    raise
                except (OSError, urllib.error.URLError, BridgeError) as e:
                    if attempt == 2:
                        raise BridgeError("RESOURCE_DOWNLOAD", f"资源阶段失败：{host} · {entry['path']} · {e}", True) from e
                    if cancelled.wait(2 ** attempt):
                        raise Cancelled()
        completed += entry["size"]
        notify(dict(stage="resources", file=entry["path"], completed=completed, total=total))
    return dict(ready=True, files=len(files), bytes=total, downloadedBytes=downloaded)
