#!/usr/bin/env python3
"""iOS Share-Sheet inbox: batch upload from a Shortcut, tracked per-file with progress.

A Shortcut POSTs a batch (photos / PDFs) plus `kind` (receipt|statement) and, for statements,
`account`. We save the files, create a job with one item per file, and process them in a
background thread — routing each to the receipt or statement pipeline. The app polls the job to
show progress: queued → processing → done | failed.

Storage, per family, under business/inbox/<job_id>/:
    job.json           the job record (kind, account, items + statuses)
    NN_<filename>      the raw uploaded files (kept until the item succeeds; used by retry)
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import business
import statements

KINDS = ("receipt", "statement")


def inbox_dir(family_id: str) -> Path:
    return business.business_dir(family_id) / "inbox"


def job_dir(family_id: str, job_id: str) -> Path:
    return inbox_dir(family_id) / job_id


def _job_file(family_id: str, job_id: str) -> Path:
    return job_dir(family_id, job_id) / "job.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe(name: str) -> str:
    name = Path(name or "file").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "file"


def sniff_ext(b: bytes) -> str:
    """Detect a file's extension from its magic bytes (so a base64 upload doesn't need a
    correct filename). Returns e.g. '.jpg', '.png', '.pdf', '.heic', or '' if unknown."""
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[:5] == b"%PDF-" or b"%PDF-" in b[:2048]:
        return ".pdf"
    if b[4:8] == b"ftyp" and any(brand in b[8:24] for brand in (b"heic", b"heix", b"mif1", b"heif", b"hevc")):
        return ".heic"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    return ""


def _read_job(family_id: str, job_id: str) -> dict | None:
    f = _job_file(family_id, job_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        return None


def _write_job(family_id: str, job: dict) -> None:
    d = job_dir(family_id, job["id"])
    d.mkdir(parents=True, exist_ok=True)
    f = _job_file(family_id, job["id"])
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
    tmp.replace(f)   # atomic → polled reads never see a partial file


def create_job(family_id: str, kind: str, account: str | None,
               files: list[tuple[str, bytes]]) -> dict:
    """files: list of (filename, bytes). Saves the files + a job record (all items queued)."""
    if kind not in KINDS:
        raise business.ParseError("kind must be 'receipt' or 'statement'.", 400)
    if kind == "statement" and account not in statements.ACCOUNTS:
        raise business.ParseError("a valid account is required for statements.", 400)
    if not files:
        raise business.ParseError("no files in the upload.", 400)

    job_id = str(uuid.uuid4())
    d = job_dir(family_id, job_id)
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for i, (fn, data) in enumerate(files):
        stored = f"{i:02d}_{_safe(fn)}"
        (d / stored).write_bytes(data)
        items.append({"index": i, "filename": fn or stored, "stored": stored,
                      "status": "queued", "result_id": None, "note": None, "error": None})
    job = {"id": job_id, "kind": kind,
           "account": account if kind == "statement" else None,
           "created_at": _now(), "items": items}
    _write_job(family_id, job)
    return job


def job_exists(family_id: str, job_id: str) -> bool:
    return _read_job(family_id, job_id) is not None


# Per-job lock: the worker and concurrent appends both read-modify-write job.json, and a stale
# write was clobbering files appended mid-process. Every mutation now goes through _mutate.
_job_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(job_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _job_locks.get(job_id)
        if lk is None:
            lk = _job_locks[job_id] = threading.Lock()
        return lk


def _mutate(family_id: str, job_id: str, fn):
    """Read job.json, apply fn(job) in place, write it back — all under the job's lock."""
    with _lock_for(job_id):
        job = _read_job(family_id, job_id)
        if job is None:
            return None
        fn(job)
        _write_job(family_id, job)
        return job


def append_files(family_id: str, job_id: str, files: list[tuple[str, bytes]]) -> dict | None:
    """Add more files (queued) to an existing job — used when an iOS Shortcut loops and POSTs
    one file per request into the same batch. Returns the updated job."""
    def add(job):
        d = job_dir(family_id, job_id)
        start = len(job["items"])
        for k, (fn, data) in enumerate(files):
            i = start + k
            stored = f"{i:02d}_{_safe(fn)}"
            (d / stored).write_bytes(data)
            job["items"].append({"index": i, "filename": fn or stored, "stored": stored,
                                 "status": "queued", "result_id": None, "note": None, "error": None})
    return _mutate(family_id, job_id, add)


def _process_item(family_id: str, kind: str, account: str | None,
                  path: Path, filename: str) -> tuple[str | None, str | None]:
    """Route one file to its pipeline. Returns (result_id, note). Raises on failure."""
    data = path.read_bytes()
    if kind == "receipt":
        rec = business.ingest_upload(family_id, filename, data)
        return rec.get("id"), ("already uploaded" if rec.get("duplicate") else None)
    rec = statements.ingest_statement(family_id, account, filename, data)
    note = f"{rec.get('parsed_count', 0)} txns"
    if rec.get("duplicates_skipped"):
        note += f", {rec['duplicates_skipped']} dup"
    if rec.get("reconciled") is False:
        note += " · ⚠ check balance"
    return rec.get("id"), note


# One worker thread drains a job's queue. New files appended mid-run (Shortcut loop) are picked
# up by the running worker; `_active` ensures we never start two workers on the same job.
_active: set[str] = set()
_active_lock = threading.Lock()


def start_processing(family_id: str, job_id: str) -> None:
    with _active_lock:
        if job_id in _active:
            return                      # a worker is already draining this job
        _active.add(job_id)
    threading.Thread(target=_drain, args=(family_id, job_id), daemon=True).start()


def _drain(family_id: str, job_id: str) -> None:
    try:
        while True:
            # Claim the next queued item under the lock (mark it processing).
            claimed: dict = {}
            def claim(job):
                it = next((i for i in job["items"] if i["status"] == "queued"), None)
                if it:
                    it["status"] = "processing"
                    claimed.update(index=it["index"], stored=it["stored"], filename=it["filename"],
                                   kind=job["kind"], account=job["account"])
            job = _mutate(family_id, job_id, claim)
            if job is None:
                return
            if not claimed:                     # nothing queued — check again, then exit
                with _lock_for(job_id):
                    j2 = _read_job(family_id, job_id)
                    if j2 and any(i["status"] == "queued" for i in j2["items"]):
                        continue
                return
            # Process OUTSIDE the lock (Claude call is slow); then write just this item back.
            try:
                rid, note = _process_item(family_id, claimed["kind"], claimed["account"],
                                          job_dir(family_id, job_id) / claimed["stored"], claimed["filename"])
                outcome = ("done", rid, note, None)
            except Exception as e:              # ParseError or anything else — never kill the worker
                outcome = ("failed", None, None, str(e)[:200])

            def finish(job):
                for i in job["items"]:
                    if i["index"] == claimed["index"]:
                        i["status"], i["result_id"], i["note"], i["error"] = outcome
            _mutate(family_id, job_id, finish)
    finally:
        with _active_lock:
            _active.discard(job_id)


def retry_item(family_id: str, job_id: str, index: int) -> dict | None:
    job = _read_job(family_id, job_id)
    if not job:
        return None
    found = False
    for item in job["items"]:
        if item["index"] == index and item["status"] == "failed":
            item.update(status="queued", error=None)
            found = True
    if not found:
        return job
    _write_job(family_id, job)
    start_processing(family_id, job_id)
    return job


def load_jobs(family_id: str, limit: int = 30) -> list[dict]:
    d = inbox_dir(family_id)
    if not d.exists():
        return []
    jobs = []
    for jf in d.glob("*/job.json"):
        try:
            jobs.append(json.loads(jf.read_text()))
        except json.JSONDecodeError:
            continue
    jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return jobs[:limit]
