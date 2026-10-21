import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


class Store:
    def __init__(self, root: Path, recover=True):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = self.root / "jobs"
        self.jobs.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "cache.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        # A previous process cannot still own a worker after stdio closes.
        for job in self.all_jobs() if recover else []:
            if job["state"] in ("running", "starting", "cancelling"):
                job["state"] = "interrupted"
                self.save_job(job)

    def job_dir(self, job_id: str):
        if not job_id or any(c not in "0123456789abcdef-" for c in job_id):
            raise ValueError("Invalid job ID")
        return self.jobs / job_id

    def all_jobs(self):
        result = []
        for p in self.jobs.glob("*/job.json"):
            try:
                result.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda j: j.get("createdAt", 0), reverse=True)

    def save_job(self, job):
        with self.lock:
            atomic_json(self.job_dir(job["id"]) / "job.json", job)

    def get_job(self, job_id):
        with self.lock:
            return json.loads((self.job_dir(job_id) / "job.json").read_text(encoding="utf-8"))

    def cache_get(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM translations WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def cache_put(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO translations VALUES (?, ?)", (key, value))
            self.db.commit()

    def close(self):
        self.db.close()
