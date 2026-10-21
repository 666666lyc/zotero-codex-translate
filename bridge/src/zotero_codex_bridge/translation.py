"""Caching and validation independent of the PDF library."""
import hashlib
import json
import re
import threading
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
    def __init__(self, store, client_factory, document_hash, model, cancel=None, notify=None):
        self.store = store
        self.client_factory = client_factory
        self.document_hash = document_hash
        self.model = model
        self.cancel = cancel or threading.Event()
        self.notify = notify or (lambda *_: None)
        self.client = None
        self.lock = threading.Lock()
        self.error = None
        self.completed = 0

    def translate(self, source):
        # BabelDOC may invoke this on worker threads. Serialize inference and
        # stop all later requests after the first terminal failure.
        with self.lock:
            if self.error:
                raise self.error
            if self.cancel.is_set():
                raise Cancelled()
            key = hashlib.sha256(json.dumps([PROMPT_VERSION, self.document_hash, self.model, source], ensure_ascii=False).encode()).hexdigest()
            cached = self.store.cache_get(key)
            if cached is not None:
                return validate_translation(source, cached)
            for attempt in range(3):
                try:
                    if self.client is None:
                        self.client = self.client_factory()
                    result = validate_translation(source, self.client.translate(source, self.model))
                    self.store.cache_put(key, result)
                    self.completed += 1
                    self.notify({"stage": "translating", "completed": self.completed, "total": None})
                    return result
                except BridgeError as e:
                    if self.client:
                        self.client.close()
                        self.client = None
                    if not e.retryable or attempt == 2:
                        self.error = e
                        raise
                    if self.cancel.wait(2 ** attempt):
                        raise Cancelled()

    def close(self):
        if self.client:
            self.client.close()
