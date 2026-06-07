#!/usr/bin/env python3
"""Phase 0 — the extraction pipeline.

Take one file (statement, screenshot, receipt, note), send it to Claude with the
extraction prompt, and write the extracted events as JSON into the staging folder
for later human review.

    python extract.py path/to/statement.pdf

Supported: PDF, JPG/JPEG, PNG, HEIC, TXT, MD.

This is the highest-leverage code in the project. Everything downstream is just
plumbing over the events this produces. Iterate on prompts/extraction_prompt.md
until the output is trustworthy on real documents.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
import paths

# Canonical event field order (program-stamped fields are filled in by us, not the model).
MODEL_FIELDS = (
    "date", "account", "type", "description",
    "amount", "currency", "category", "confidence", "source_snippet",
)

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TEXT_SUFFIXES = {".txt", ".md", ".csv"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".heic"} | set(IMAGE_MEDIA_TYPES)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_prompt() -> str:
    if not paths.PROMPT_FILE.exists():
        die(f"extraction prompt not found at {paths.PROMPT_FILE}")
    return paths.PROMPT_FILE.read_text()


def heic_to_jpeg(src: Path) -> Path:
    """Convert HEIC to JPEG using macOS's built-in `sips` (Claude can't read HEIC)."""
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    import os
    os.close(fd)
    out = Path(tmp)
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(src), "--out", str(out)],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        die("`sips` not found — HEIC conversion requires macOS. Convert to JPG/PNG first.")
    except subprocess.CalledProcessError as e:
        die(f"HEIC conversion failed: {e.stderr.decode(errors='replace')}")
    return out


def build_content_block(path: Path) -> dict:
    """Build the Anthropic content block for the file (document/image/text)."""
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        text = path.read_text(errors="replace")
        return {"type": "text", "text": f"## Document ({path.name})\n\n{text}"}

    if suffix == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }

    if suffix == ".heic":
        jpeg = heic_to_jpeg(path)
        data = base64.standard_b64encode(jpeg.read_bytes()).decode()
        jpeg.unlink(missing_ok=True)
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        }

    if suffix in IMAGE_MEDIA_TYPES:
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": IMAGE_MEDIA_TYPES[suffix], "data": data},
        }

    die(f"unsupported file type: {suffix or '(none)'} — supported: pdf, jpg, png, heic, txt, md")


def parse_events(text: str) -> list[dict]:
    """Parse the model's reply into a list of event dicts, defensively."""
    text = text.strip()
    # Strip accidental code fences.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: grab the outermost array.
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end < start:
            die("model did not return parseable JSON. Raw reply:\n\n" + text)
        data = json.loads(text[start:end + 1])

    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    if not isinstance(data, list):
        die("expected a JSON array of events, got: " + type(data).__name__)
    return data


def normalize_event(
    raw: dict, source_file: str, extracted_at: str,
    family_id: str, member_id: str | None,
) -> dict:
    """Force the canonical event shape; program stamps id/family/source/status."""
    return {
        "id": str(uuid.uuid4()),
        "family_id": family_id,
        "member_id": member_id,
        "date": raw.get("date"),
        "account": raw.get("account"),
        "type": raw.get("type"),
        "description": raw.get("description"),
        "amount": raw.get("amount"),
        "currency": raw.get("currency"),
        "category": raw.get("category"),
        "confidence": raw.get("confidence"),
        "source_file": source_file,
        "source_snippet": raw.get("source_snippet"),
        "extracted_at": extracted_at,
        "review_status": "pending",
        "reviewer_notes": None,
    }


def extract(path: Path, family_id: str, member_id: str | None = None) -> Path:
    app_cfg = config.load_app_config()
    api_key = config.load_api_key()
    if not api_key:
        die(
            "no ANTHROPIC_API_KEY found.\n"
            f"  Add it to {paths.ENV_FILE} as:  ANTHROPIC_API_KEY=sk-ant-...\n"
            "  (or export it in your shell), then re-run."
        )

    try:
        from anthropic import Anthropic
    except ImportError:
        die("anthropic SDK not installed. Run: pip install -r requirements.txt")

    prompt = load_prompt()
    content_block = build_content_block(path)
    model = app_cfg["extraction"]["model"]
    max_tokens = app_cfg["extraction"]["max_tokens"]

    who = f"{family_id}/{member_id}" if member_id else family_id
    print(f"→ extracting {path.name} for [{who}] with {model} ...")
    t0 = time.time()
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            # Document/image first, then the instruction — recommended ordering.
            "content": [content_block, {"type": "text", "text": prompt}],
        }],
    )
    elapsed = time.time() - t0

    reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    raw_events = parse_events(reply)

    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    events = [normalize_event(e, path.name, extracted_at, family_id, member_id) for e in raw_events]

    # Write staging file: YYYY-MM-DD-<hash>.json with provenance wrapper.
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = paths.staging(family_id) / f"{date_part}-{file_hash}.json"
    payload = {
        "family_id": family_id,
        "member_id": member_id,
        "source_file": path.name,
        "extracted_at": extracted_at,
        "model": model,
        "event_count": len(events),
        "events": events,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    _print_summary(events, out_path, msg, elapsed)
    return out_path


def _print_summary(events: list[dict], out_path: Path, msg, elapsed: float) -> None:
    flag = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    print(f"\n✓ {len(events)} event(s) extracted in {elapsed:.1f}s\n")
    for e in events:
        c = flag.get(e["confidence"], "⚪️")
        amt = e["amount"]
        amt_s = f"{amt:>12,.2f}" if isinstance(amt, (int, float)) else f"{'?':>12}"
        cur = e["currency"] or ""
        desc = (e["description"] or "(no description)")[:40]
        print(f"  {c} {e['date'] or '????-??-??'}  {amt_s} {cur:<3}  {desc}")
    print(f"\n  staged → {out_path}")
    usage = getattr(msg, "usage", None)
    if usage:
        print(f"  tokens: {usage.input_tokens} in / {usage.output_tokens} out")
    low = sum(1 for e in events if e["confidence"] == "low")
    if low:
        print(f"\n  ⚠ {low} event(s) at LOW confidence — check these against the source.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract financial events from one file.")
    ap.add_argument("file", help="path to a statement / screenshot / receipt / note")
    ap.add_argument("--family", "-f", help="family id (defaults to the active family)")
    ap.add_argument("--member", "-m", help="member id this document belongs to (optional)")
    args = ap.parse_args()

    path = Path(args.file).expanduser()
    if not path.exists():
        die(f"file not found: {path}")
    if not path.is_file():
        die(f"not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        die(f"unsupported file type: {path.suffix.lower() or '(none)'} "
            "— supported: pdf, jpg, png, heic, txt, md")

    family_id = config.resolve_family(args.family)
    if not family_id:
        die("no family specified and no active family set.\n"
            "  Create one:   python families.py create <id> --name \"...\"\n"
            "  Or pass:      python extract.py <file> --family <id>")
    if not paths.family_exists(family_id):
        existing = ", ".join(paths.list_families()) or "(none)"
        die(f"no such family: {family_id}. Existing: {existing}")

    if args.member:
        member_ids = {m.get("id") for m in config.load_members(family_id)}
        if args.member not in member_ids:
            known = ", ".join(sorted(member_ids)) or "(none)"
            die(f"no member '{args.member}' in family '{family_id}'. Known: {known}")

    paths.ensure_family_dirs(family_id)
    extract(path, family_id, args.member)


if __name__ == "__main__":
    main()
