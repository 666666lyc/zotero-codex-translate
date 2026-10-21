"""Caching and validation independent of the PDF library."""
import hashlib
import json
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from collections import Counter

from .errors import BridgeError, Cancelled

PROMPT_VERSION = "academic-zh-v1"
TOKENS = re.compile(r"\{v\d+\}|</?style(?:\s+id=['\"]\d+['\"])?\s*>|</?b\d+>")


def validate_translation(source, result):
    if not isinstance(result, str) or not result.strip():
        raise BridgeError("EMPTY_TRANSLATION", "模型返回空译文。", True)
    if Counter(TOKENS.findall(source)) != Counter(TOKENS.findall(result)):
        raise BridgeError("PLACEHOLDER_MISMATCH", "译文中的公式或样式标记损坏。", True)
    if re.search(r"[A-Za-z]{3,}", source) and not re.search(r"[\u3400-\u9fff]", result):
        # Isolated names, URLs, and citation markers need no Chinese translation.
        if len(re.findall(r"[A-Za-z]{3,}", source)) >= 5:
            raise BridgeError("UNTRANSLATED", "段落未翻译为中文。", True)
    return result


class TranslationService:
    def __init__(self, store, client_factory, document_hash, model, cancel=None, notify=None, cache_only=False):
        self.store = store
        self.client_factory = client_factory
        self.document_hash = document_hash
        self.model = model
        self.cache_only = cache_only
        self.cancel = cancel or threading.Event()
        self.notify = notify or (lambda *_: None)
        self.client = None
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.pending = deque()
        self.inflight = {}
        self.dispatcher = None
        self.stopped = False
        self.started = time.monotonic()
        self.stats = {"paragraphs": 0, "cacheHits": 0, "requests": 0, "retries": 0, "modelSeconds": 0.0}
        self.waiting_since = None
        self.error = None
        self.completed = 0

    def translate(self, source):
        # Only queue/cache bookkeeping is locked; network IO belongs to a single
        # dispatcher. BabelDOC preparation threads can now form real batches.
        key = hashlib.sha256(json.dumps([PROMPT_VERSION, self.document_hash, self.model, source], ensure_ascii=False).encode()).hexdigest()
        with self.condition:
            if self.error:
                raise self.error
            if self.cancel.is_set() or self.stopped:
                raise Cancelled()
            self.stats["paragraphs"] += 1
            cached = self.store.cache_get(key)
            if cached is not None:
                try:
                    result = validate_translation(source, cached)
                except BridgeError:
                    pass  # An invalid old entry is never treated as success.
                else:
                    self.stats["cacheHits"] += 1
                    self.completed += 1
                    self.progress("cache")
                    return result
            if self.cache_only:
                self.error = BridgeError("CACHE_INCOMPLETE", "此任务所需的有效译文缓存不完整；未调用模型，请选择正常翻译以补齐缓存。")
                raise self.error
            future = self.inflight.get(key)
            if future is None:
                future = Future()
                self.inflight[key] = future
                self.pending.append((key, source, future))
                if self.dispatcher is None:
                    self.dispatcher = threading.Thread(target=self._dispatch, daemon=True)
                    self.dispatcher.start()
                self.condition.notify_all()
        while True:
            try:
                result = future.result(timeout=0.1)
                with self.lock:
                    self.completed += 1
                    self.progress()
                return result
            except TimeoutError:
                if self.cancel.is_set():
                    raise Cancelled()

    def metrics(self):
        with self.lock:
            return {**self.stats, "modelSeconds": round(self.stats["modelSeconds"], 3),
                    "elapsedSeconds": round(time.monotonic() - self.started, 3),
                    "waitingSeconds": round(time.monotonic() - self.waiting_since, 1) if self.waiting_since else 0}

    def progress(self, stage="translating"):
        self.notify({"stage": stage, "completed": self.completed, "total": None, "metrics": self.metrics()})

    @staticmethod
    def token_estimate(text):
        # Conservative bound with no model-specific tokenizer download.
        return max(1, (len(text.encode("utf-8")) + 2) // 3)

    def _call(self, batch, single=False):
        if self.cancel.is_set():
            raise Cancelled()
        if self.client is None:
            self.client = self.client_factory()
        start = time.monotonic()
        with self.lock:
            self.stats["requests"] += 1
            self.waiting_since = start
        try:
            if single or not hasattr(self.client, "translate_batch"):
                key, text, _ = batch[0]
                return {key: self.client.translate(text, self.model)}
            rows = self.client.translate_batch([{"id": k, "text": s} for k, s, _ in batch], self.model)
            if not isinstance(rows, list):
                raise BridgeError("INVALID_OUTPUT", "批量译文格式不完整。", True)
            values = {}
            duplicates = set()
            for row in rows:
                if not isinstance(row, dict) or row.get("id") not in {k for k, _, _ in batch}:
                    continue
                key = row["id"]
                if key in values:
                    duplicates.add(key)
                values[key] = row.get("text")
            for key in duplicates:
                values.pop(key, None)
            return values
        finally:
            with self.lock:
                self.stats["modelSeconds"] += time.monotonic() - start
                self.waiting_since = None

    def _reset_client(self):
        if self.client:
            self.client.close()
            self.client = None

    def _resolve_batch(self, batch):
        remaining = batch
        for attempt in range(3):
            if attempt:
                with self.lock:
                    self.stats["retries"] += len(remaining)
                if self.cancel.wait(2 ** (attempt - 1)):
                    raise Cancelled()
            failed = []
            groups = [remaining] if attempt == 0 and hasattr(self.client, "translate_batch") else [[row] for row in remaining]
            # Construct the client before capability selection on the first batch.
            if self.client is None:
                self.client = self.client_factory()
                groups = [remaining] if attempt == 0 and hasattr(self.client, "translate_batch") else [[row] for row in remaining]
            for group in groups:
                try:
                    values = self._call(group, single=attempt > 0)
                except BridgeError as error:
                    if not error.retryable:
                        raise
                    self._reset_client()
                    failed.extend((row, error) for row in group)
                    continue
                for row in group:
                    key, text, future = row
                    try:
                        value = validate_translation(text, values.get(key))
                        self.store.cache_put(key, value)
                        if not future.done():
                            future.set_result(value)
                    except BridgeError as error:
                        failed.append((row, error))
            if not failed:
                return
            remaining = [row for row, _ in failed]
        raise failed[0][1]

    def _dispatch(self):
        while True:
            with self.condition:
                while not self.pending and not self.stopped:
                    self.condition.wait(0.1)
                if self.stopped:
                    return
                deadline = time.monotonic() + 0.075
                while len(self.pending) < 8 and time.monotonic() < deadline and not self.stopped:
                    self.condition.wait(max(0, deadline - time.monotonic()))
                if self.stopped:
                    return
                batch, tokens = [], 0
                while self.pending and len(batch) < 8:
                    row = self.pending[0]
                    estimate = self.token_estimate(row[1])
                    if batch and tokens + estimate > 4000:
                        break
                    batch.append(self.pending.popleft())
                    tokens += estimate
            try:
                self._resolve_batch(batch)
            except BaseException as error:
                if not isinstance(error, BridgeError):
                    error = BridgeError("TRANSLATION_FAILED", f"翻译调度失败：{type(error).__name__}")
                with self.condition:
                    self.error = error
                    for future in self.inflight.values():
                        if not future.done():
                            future.set_exception(error)
                    self.pending.clear()
                    return
            finally:
                with self.lock:
                    for key, _, _ in batch:
                        self.inflight.pop(key, None)

    def close(self):
        with self.condition:
            self.stopped = True
            for future in self.inflight.values():
                if not future.done():
                    future.set_exception(Cancelled())
            self.condition.notify_all()
        self.cancel.set()
        if self.dispatcher and self.dispatcher is not threading.current_thread():
            self.dispatcher.join(timeout=10)
        self._reset_client()
