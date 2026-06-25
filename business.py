#!/usr/bin/env python3
"""Phase 1 of the receipts → corp-tax pipeline: upload + AI parse + review.

Upload a business receipt or invoice → Claude parses it (forced-JSON via tool-use)
into a structured record → you review/edit/approve it. Nothing AI-extracted is
trusted until approved (constitution rule #5).

Storage, per family, under `business/`:
    uploads/<id>.<ext>   original file, kept as the audit trail (HEIC → JPEG so it
                         renders on the phone; the original name is kept in source_file)
    receipts.jsonl       one JSON record per line; every receipt with a review_status

Later phases (statement import, matching, categorize, export) read this ledger.
This module never calls sys.exit / extract.die in the request path — a parse failure
yields a low-confidence stub the user fills by hand, not a dead worker.
"""
from __future__ import annotations

import hashlib
import datetime as _dt
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

import config
import extract
import paths


class ParseError(Exception):
    """Upload/parse problem that maps to an HTTP status (file-level, recoverable)."""
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---------- business config + fiscal year ----------
# The corporation's fiscal year drives FY labelling, cross-FY warnings, HST periods, and the
# accountant export. Stored per family in business/config.yaml. Default: starts July 1 (so the
# year-end is June 30 — Aitee Consulting's actual FYE). FY is labelled by the year it ENDS:
# FY2026 = Jul 1 2025 → Jun 30 2026 (matches "fiscal 2025" = year ended Jun 30 2025).

DEFAULT_BUSINESS_CONFIG = {"fiscal_year_start_month": 7, "fiscal_year_start_day": 1,
                           "archived_accounts": []}


def business_config_path(family_id: str) -> Path:
    return business_dir(family_id) / "config.yaml"


def load_business_config(family_id: str) -> dict:
    cfg = dict(DEFAULT_BUSINESS_CONFIG)
    p = business_config_path(family_id)
    if p.exists():
        try:
            cfg.update(yaml.safe_load(p.read_text()) or {})
        except Exception:
            pass
    return cfg


def save_business_config(family_id: str, data: dict) -> dict:
    cfg = load_business_config(family_id)
    m, d = (data or {}).get("fiscal_year_start_month"), (data or {}).get("fiscal_year_start_day")
    if isinstance(m, int) and 1 <= m <= 12:
        cfg["fiscal_year_start_month"] = m
    if isinstance(d, int) and 1 <= d <= 31:
        cfg["fiscal_year_start_day"] = d
    if isinstance((data or {}).get("archived_accounts"), list):
        cfg["archived_accounts"] = [str(x) for x in data["archived_accounts"]]
    if isinstance((data or {}).get("accounts"), list):
        cfg["accounts"] = [str(x) for x in data["accounts"]]
    if isinstance((data or {}).get("credit_card_accounts"), list):
        cfg["credit_card_accounts"] = [str(x) for x in data["credit_card_accounts"]]
    ensure_dirs(family_id)
    business_config_path(family_id).write_text(yaml.safe_dump(cfg, sort_keys=False))
    return cfg


def family_has_business(family_id: str) -> bool:
    """Whether this family has opted into the bookkeeping / corporation module.

    True if explicitly enabled in family.yaml (has_business: true), OR — for families that
    predate the flag — if business data already exists on disk. This keeps existing books
    (e.g. the household) working without a migration step, while new families start off."""
    fcfg = config.load_family_config(family_id)
    if fcfg.get("has_business"):
        return True
    if business_config_path(family_id).exists():
        return True
    for p in (receipts_path(family_id), business_dir(family_id) / "statements.jsonl"):
        if p.exists() and p.stat().st_size > 0:
            return True
    return False


def enable_business(family_id: str) -> dict:
    """Turn the bookkeeping module on for a family: set the flag and scaffold a default
    business config if none exists. Idempotent."""
    config.save_family_config(family_id, {"has_business": True})
    if not business_config_path(family_id).exists():
        # Scaffold with an explicit (empty) accounts key so a new business starts with no
        # accounts rather than inheriting the legacy built-in fallback list.
        save_business_config(family_id, {"accounts": [], "credit_card_accounts": []})
    return load_business_config(family_id)


def fiscal_year_of(date_str, cfg: dict | None = None):
    """The fiscal year (labelled by the calendar year it ENDS in) that a YYYY-MM-DD date falls
    in. Correct for any start date incl. Jan 1 (calendar-year accounting). None if unparseable."""
    cfg = cfg or DEFAULT_BUSINESS_CONFIG
    try:
        y, m, d = (int(x) for x in str(date_str)[:10].split("-"))
    except (ValueError, AttributeError):
        return None
    sm, sd = cfg.get("fiscal_year_start_month", 7), cfg.get("fiscal_year_start_day", 1)
    start_year = y if (m, d) >= (sm, sd) else y - 1   # calendar year this FY period began
    end = _dt.date(start_year + 1, sm, sd) - _dt.timedelta(days=1)
    return end.year                                   # FY label = the year it ends


def fiscal_year_bounds(fy: int, cfg: dict | None = None) -> tuple[str, str]:
    """(start_iso, end_iso) for fiscal year `fy` (labelled by end year). e.g. Jul-1 start →
    FY2026 = ('2025-07-01','2026-06-30'); Jan-1 start → FY2026 = ('2026-01-01','2026-12-31')."""
    cfg = cfg or DEFAULT_BUSINESS_CONFIG
    sm, sd = cfg.get("fiscal_year_start_month", 7), cfg.get("fiscal_year_start_day", 1)
    end = _dt.date(fy, sm, sd) - _dt.timedelta(days=1)
    if end.year != fy:                                # (1,1) case: Jan 1 - 1 day = Dec 31 prior
        end = _dt.date(fy + 1, sm, sd) - _dt.timedelta(days=1)
    next_start = end + _dt.timedelta(days=1)
    start = _dt.date(next_start.year - 1, sm, sd)
    return start.isoformat(), end.isoformat()


def fiscal_year_label(fy) -> str:
    return f"FY{fy}" if fy is not None else "—"


# ---------- per-category tax (auto-fill missing HST) ----------
# When a receipt's slip doesn't itemize tax (e.g. restaurant card slips), back the HST out of the
# tax-inclusive total using the category's rate. Defaults below; user-editable in Settings.
# Note: gift cards (often booked to Advertising and Promotion) and bank/financial fees are NOT
# taxable — defaulted off so we don't invent HST. Flip any of these in Settings → Corporation.
DEFAULT_CATEGORY_TAX = {
    "Advertising and Promotion": {"rate": 0, "taxable": False},
    "Bank charges": {"rate": 0, "taxable": False},
    "Depreciation expense": {"rate": 0, "taxable": False},
    "Internet": {"rate": 13, "taxable": True},
    "Meals and entertainment": {"rate": 13, "taxable": True},
    "Occupancy fee": {"rate": 0, "taxable": False},
    "Office expenses": {"rate": 13, "taxable": True},
    "Office supplies": {"rate": 13, "taxable": True},
    "Other expense": {"rate": 13, "taxable": True},
    "Professional fee": {"rate": 13, "taxable": True},
    "Vehicle expense": {"rate": 13, "taxable": True},
}


def load_category_tax(family_id: str) -> dict:
    """Per-category {rate, taxable}, defaults merged with any user overrides in business config."""
    out = {k: dict(v) for k, v in DEFAULT_CATEGORY_TAX.items()}
    saved = load_business_config(family_id).get("category_tax", {}) or {}
    for cat, v in saved.items():
        if not isinstance(v, dict):
            continue
        out.setdefault(cat, {"rate": 0, "taxable": False})
        if "rate" in v:
            try:
                out[cat]["rate"] = float(v["rate"])
            except (TypeError, ValueError):
                pass
        if "taxable" in v:
            out[cat]["taxable"] = bool(v["taxable"])
    return out


def save_category_tax(family_id: str, data: dict) -> dict:
    cfg = load_business_config(family_id)
    clean = {}
    for cat, v in (data or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            rate = float(v.get("rate", 0))
        except (TypeError, ValueError):
            rate = 0
        clean[str(cat)] = {"rate": rate, "taxable": bool(v.get("taxable"))}
    cfg["category_tax"] = clean
    ensure_dirs(family_id)
    business_config_path(family_id).write_text(yaml.safe_dump(cfg, sort_keys=False))
    return load_category_tax(family_id)


def _derive_tax(total: float, tip: float, rate: float) -> tuple[float, float]:
    """Back HST out of a tax-inclusive total (tip is not taxed). Returns (hst, subtotal)."""
    base = total - tip
    hst = round(base * rate / (100 + rate), 2)
    return hst, round(base - hst, 2)


def apply_tax_estimate(r: dict, taxmap: dict) -> bool:
    """If the receipt has NO captured tax and its category is taxable, estimate the HST from the
    total and mark it estimated. Returns True if it changed the record."""
    c = taxmap.get(r.get("category"))
    if not c or not c.get("taxable") or not c.get("rate"):
        return False
    if abs(sum(float(r.get(k) or 0) for k in ("gst", "pst", "hst"))) > 0.005:
        return False                              # real itemized tax already present
    total = float(r.get("total") or 0)
    if total <= 0:
        return False
    hst, subtotal = _derive_tax(total, float(r.get("tip") or 0), float(c["rate"]))
    r["hst"], r["subtotal"], r["hst_estimated"] = hst, subtotal, True
    return True


def reestimate_tax(family_id: str) -> int:
    """(Re)apply tax estimates across existing receipts. Never touches a receipt the user has
    overridden (`hst_estimated is False`) or one with real itemized tax. Returns count changed."""
    taxmap = load_category_tax(family_id)
    recs = load_receipts(family_id)
    n = 0
    for r in recs:
        if r.get("hst_estimated") is False:
            continue                              # user owns the tax on this one
        was_estimate = r.get("hst_estimated") is True
        c = taxmap.get(r.get("category"))
        if not c or not c.get("taxable") or not c.get("rate"):
            if was_estimate:                      # category turned non-taxable → undo estimate
                r["hst"] = 0.0
                r["subtotal"] = round(float(r.get("total") or 0) - float(r.get("tip") or 0), 2)
                r["hst_estimated"] = None
                n += 1
            continue
        has_tax = abs(sum(float(r.get(k) or 0) for k in ("gst", "pst", "hst"))) > 0.005
        if has_tax and not was_estimate:
            continue                              # real itemized tax — leave it
        total = float(r.get("total") or 0)
        if total <= 0:
            continue
        hst, subtotal = _derive_tax(total, float(r.get("tip") or 0), float(c["rate"]))
        if r.get("hst") != hst or r.get("subtotal") != subtotal or not was_estimate:
            r["hst"], r["subtotal"], r["hst_estimated"] = hst, subtotal, True
            n += 1
    if n:
        _write_receipts(family_id, recs)
    return n


# ---------- layout ----------

def business_dir(family_id: str) -> Path:
    return paths.family_dir(family_id) / "business"


def uploads_dir(family_id: str) -> Path:
    return business_dir(family_id) / "uploads"


def receipts_path(family_id: str) -> Path:
    return business_dir(family_id) / "receipts.jsonl"


def ensure_dirs(family_id: str) -> None:
    uploads_dir(family_id).mkdir(parents=True, exist_ok=True)


def repair_pdf(path: Path) -> Path:
    """Make a PDF the Anthropic API will accept. Some bank e-statements (Fiserv 'PDF Export')
    ship with a junk prefix before the '%PDF-' header (the file even mis-detects as Java
    serialization), which the API rejects as invalid. If the file isn't already a clean PDF,
    rebuild it with Ghostscript; fall back to trimming bytes before the first %PDF / after the
    last %%EOF. Repairs in place (the stored audit copy becomes the clean one). Returns `path`."""
    data = path.read_bytes()
    if data[:5] == b"%PDF-":
        return path
    idx = data.find(b"%PDF-")
    if idx == -1:
        raise ParseError("File is not a readable PDF.", 400)
    gs = shutil.which("gs")
    if gs:
        out = path.with_suffix(".clean.pdf")
        try:
            subprocess.run([gs, "-q", "-o", str(out), "-sDEVICE=pdfwrite", str(path)],
                           check=True, capture_output=True, timeout=180)
            if out.exists() and out.stat().st_size > 0:
                out.replace(path)
                return path
        except Exception:
            out.unlink(missing_ok=True)
    # Fallback: strip the wrapper around the embedded PDF.
    end = data.rfind(b"%%EOF")
    path.write_bytes(data[idx: end + 5] if end != -1 else data[idx:])
    return path


# ---------- categories ----------

# The accountant's exact FY2025 buckets — categorize into THESE, not generic labels,
# so the export drops straight into the corp filing. See memory corp-chart-of-accounts.
EXPENSE_CATEGORIES = [
    "Advertising and Promotion", "Bank charges", "Depreciation expense", "Internet",
    "Meals and entertainment", "Occupancy fee", "Office expenses", "Office supplies",
    "Other expense", "Professional fee", "Vehicle expense",
]
INCOME_CATEGORIES = ["Revenue", "Other income"]
ALL_CATEGORIES = EXPENSE_CATEGORIES + INCOME_CATEGORIES


# ---------- AI parse ----------

# Forces Claude to return exactly the receipt-record fields (design doc §2).
RECEIPT_TOOL = {
    "name": "extract_receipt",
    "description": "Extract the structured fields from this business receipt or invoice image/PDF.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["receipt", "invoice"],
                     "description": "'invoice' for a billing document; 'receipt' for a point-of-sale purchase proof."},
            "direction": {"type": "string", "enum": ["expense", "income"],
                          "description": "'expense' = money the business paid out. 'income' = an invoice you ISSUED to a client (money earned)."},
            "vendor": {"type": "string", "description": "Merchant/supplier name; for income, the client billed."},
            "date": {"type": "string", "description": "Transaction date as YYYY-MM-DD. Empty string if not legible."},
            "currency": {"type": "string", "description": "ISO code, e.g. CAD or USD. Assume CAD if not shown."},
            "subtotal": {"type": "number", "description": "Pre-tax amount."},
            "tip": {"type": "number", "description": "Gratuity; 0 if none."},
            "gst": {"type": "number", "description": "GST shown; 0 if none."},
            "pst": {"type": "number", "description": "PST/QST shown; 0 if none."},
            "hst": {"type": "number", "description": "HST shown; 0 if none."},
            "total": {"type": "number", "description": "Grand total actually paid or billed."},
            "payment_method": {"type": "string", "description": "e.g. 'Amex ••1234', 'Visa', 'Cash'. Empty if not shown."},
            "category": {"type": "string", "enum": ALL_CATEGORIES,
                         "description": "Pick the ONE bucket this belongs to. Mapping hints: fuel/gas/parking/"
                         "taxi/car repair/auto → 'Vehicle expense'; restaurant/coffee/food → 'Meals and "
                         "entertainment'; client gifts (e.g. Costco/Shoppers purchases) → 'Advertising and "
                         "Promotion'; paper/cables/physical supplies → 'Office supplies'; software/subscriptions/"
                         "general office → 'Office expenses'; internet/phone → 'Internet'; legal/accounting/"
                         "consultant → 'Professional fee'; bank or card fees → 'Bank charges'. Use 'Other expense' "
                         "only if nothing fits. For an invoice you ISSUED (income), use 'Revenue'."},
            "line_items": {"type": "array", "items": {
                "type": "object",
                "properties": {"desc": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["desc", "amount"]}},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "confidence_reason": {"type": "string", "description": "If confidence is medium or low, ONE short plain "
                                  "sentence (max ~14 words) saying what to double-check — e.g. 'Tax line unclear; "
                                  "HST may be missing.', 'Total faint and partly cut off.', 'Date hard to read.', "
                                  "'Handwritten amount.'. Empty string when confidence is high."},
            "source_snippet": {"type": "string", "description": "Short verbatim snippet evidencing the total, e.g. 'TOTAL 113.00'."},
        },
        "required": ["kind", "direction", "vendor", "date", "currency", "subtotal",
                     "tip", "gst", "pst", "hst", "total", "category", "confidence", "confidence_reason"],
    },
}

_PARSE_PROMPT = (
    "You are a Canadian (Ontario) bookkeeping assistant. Read this business receipt or "
    "invoice and extract its fields via the extract_receipt tool. Be precise with the "
    "money: subtotal + gst + pst + hst + tip must equal total (if they don't, re-read the "
    "amounts). Identify the sales-tax breakdown separately — Ontario purchases usually show "
    "13% HST. Decide direction: an invoice you ISSUED to a client is income; anything you "
    "bought is an expense. Choose category from the fixed accountant buckets in the tool enum — "
    "note client gifts go to 'Advertising and Promotion' and anything vehicle/fuel goes to "
    "'Vehicle expense'. If a value isn't legible, use 0 for money / empty string for text and "
    "lower the confidence. Don't invent values. When confidence is medium or low, set "
    "confidence_reason to one short, plain sentence telling the user what to double-check."
)


def _convert_heic(src: Path) -> Path:
    """HEIC → JPEG via macOS `sips`, in place (Claude and browsers can't read HEIC).
    Returns the new .jpg path; raises ParseError on failure (never sys.exit)."""
    out = src.with_suffix(".jpg")
    try:
        subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(out)],
                       check=True, capture_output=True)
    except FileNotFoundError:
        raise ParseError("HEIC conversion needs macOS `sips`; upload JPG/PNG instead.", 400)
    except subprocess.CalledProcessError as e:
        raise ParseError(f"HEIC conversion failed: {e.stderr.decode(errors='replace')}", 400)
    src.unlink(missing_ok=True)
    return out


def parse_receipt(path: Path, family_id: str) -> dict:
    """Send one file to Claude and return the raw extracted fields. Raises ParseError."""
    api_key = config.load_api_key()
    if not api_key:
        raise ParseError("No ANTHROPIC_API_KEY set in the data-root .env.", 400)
    try:
        from anthropic import Anthropic
    except ImportError:
        raise ParseError("anthropic SDK not installed on the server.", 500)

    content_block = extract.build_content_block(path)  # suffix pre-validated by caller
    model = config.load_app_config()["extraction"]["model"]
    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model, max_tokens=2000,
            tools=[RECEIPT_TOOL],
            tool_choice={"type": "tool", "name": "extract_receipt"},
            messages=[{"role": "user", "content": [content_block,
                                                   {"type": "text", "text": _PARSE_PROMPT}]}],
        )
    except Exception as e:  # network / auth / rate-limit
        raise ParseError(f"Claude request failed: {e}", 502)

    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        raise ParseError("Claude did not return structured receipt data.", 502)
    out = dict(block.input)
    out["model"] = model
    return out


# ---------- records ----------

def _stamp(raw: dict, rid: str, source_file: str, stored_ext: str) -> dict:
    """Force the canonical receipt-record shape; program stamps id/status/timestamps."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    def num(k):
        v = raw.get(k)
        return float(v) if isinstance(v, (int, float)) else 0.0
    return {
        "id": rid,
        "kind": raw.get("kind") or "receipt",
        "direction": raw.get("direction") or "expense",
        "vendor": raw.get("vendor") or "",
        "date": raw.get("date") or "",
        "currency": (raw.get("currency") or "CAD").upper(),
        "subtotal": num("subtotal"), "tip": num("tip"),
        "gst": num("gst"), "pst": num("pst"), "hst": num("hst"),
        "total": num("total"),
        "payment_method": raw.get("payment_method") or "",
        "category": raw.get("category") or "",
        "line_items": raw.get("line_items") if isinstance(raw.get("line_items"), list) else [],
        "matched_txn_id": None,
        "confidence": raw.get("confidence") or "low",
        "confidence_reason": raw.get("confidence_reason") or "",
        "hst_estimated": None,       # None=never estimated · True=auto-estimate · False=user override
        "review_status": "pending",
        "source_file": source_file,
        "stored_ext": stored_ext,
        "content_hash": None,        # sha256 of the original upload bytes (dedup); set by caller
        "source_snippet": raw.get("source_snippet") or "",
        "model": raw.get("model"),
        "uploaded_at": now,
        "notes": raw.get("notes"),
    }


def load_receipts(family_id: str) -> list[dict]:
    """All receipt records, newest upload first."""
    f = receipts_path(family_id)
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.sort(key=lambda r: r.get("uploaded_at") or "", reverse=True)
    return out


# Higher-priority flags that mean "needs your attention" (vs a routine note like a card slip).
ATTENTION_FLAGS = ("duplicate", "refund", "low_confidence", "tax_mismatch")


def receipt_flags(r: dict) -> list[str]:
    """Derived flags for the review UI: duplicate, refund, low_confidence, tax_mismatch, has_note."""
    flags = []
    reason = str(r.get("confidence_reason") or "").lower()
    total = float(r.get("total") or 0)
    if "duplicate" in reason:
        flags.append("duplicate")
    if "refund" in reason or "return" in reason or total < 0:
        flags.append("refund")
    if r.get("confidence") == "low":
        flags.append("low_confidence")
    s = sum(float(r.get(k) or 0) for k in ("subtotal", "gst", "pst", "hst", "tip"))
    if abs(round(s - total, 2)) > 0.02:
        flags.append("tax_mismatch")
    if r.get("confidence_reason"):
        flags.append("has_note")
    return flags


def _write_receipts(family_id: str, records: list[dict]) -> None:
    ensure_dirs(family_id)
    f = receipts_path(family_id)
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def _append_receipt(family_id: str, record: dict) -> None:
    ensure_dirs(family_id)
    with receipts_path(family_id).open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def ingest_upload(family_id: str, filename: str, data: bytes) -> dict:
    """Save one uploaded file, parse it, append + return the record.

    A parse failure does NOT raise for unsupported-but-recoverable cases: any
    successfully-stored file that Claude can't read yields a low-confidence stub the
    user fills by hand. Only a genuinely unusable upload (bad type) raises ParseError.
    """
    ensure_dirs(family_id)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in extract.SUPPORTED_SUFFIXES:
        raise ParseError(f"Unsupported file type '{suffix or '(none)'}' — use PDF, JPG, PNG, or HEIC.", 400)

    # Exact-duplicate guard: hash the ORIGINAL upload bytes (pre-HEIC-convert) and short-circuit
    # if we've seen this exact file before — no second copy, no wasted Claude call. Returns the
    # existing record tagged with a transient `duplicate` flag for the UI.
    data_hash = hashlib.sha256(data).hexdigest()
    for r in load_receipts(family_id):
        if r.get("content_hash") == data_hash:
            dup = dict(r)
            dup["duplicate"] = True
            return dup

    rid = str(uuid.uuid4())
    stored = uploads_dir(family_id) / f"{rid}{suffix}"
    stored.write_bytes(data)
    if suffix == ".heic":
        stored = _convert_heic(stored)          # may raise ParseError (bad HEIC)
    elif suffix == ".pdf":
        stored = repair_pdf(stored)             # fix Fiserv-style junk-prefixed PDFs

    try:
        raw = parse_receipt(stored, family_id)
    except ParseError as e:
        # File is stored fine; AI just couldn't read it. Stub it for manual entry.
        raw = {"confidence": "low", "vendor": "", "notes": f"AI parse failed: {e}"}

    record = _stamp(raw, rid, source_file=filename or stored.name, stored_ext=stored.suffix.lower())
    record["content_hash"] = data_hash
    apply_tax_estimate(record, load_category_tax(family_id))   # back out HST if slip didn't itemize it
    _append_receipt(family_id, record)
    return record


def backfill_hashes(family_id: str) -> int:
    """Fill content_hash for records uploaded before dedup existed, by hashing their stored
    file. Exact for PDF/JPG/PNG (stored == original); HEIC was converted so its hash reflects
    the JPEG, not the original — still dedups re-uploads of that same JPEG. Returns count filled."""
    records = load_receipts(family_id)
    filled = 0
    for r in records:
        if r.get("content_hash"):
            continue
        p = upload_path(family_id, r.get("id"))
        if p and p.exists():
            r["content_hash"] = hashlib.sha256(p.read_bytes()).hexdigest()
            filled += 1
    if filled:
        _write_receipts(family_id, records)
    return filled


# Fields the AI fills (vs program-stamped id/status/hash/timestamps) — used by reparse_failed.
_PARSED_FIELDS = ("kind", "direction", "vendor", "date", "currency", "subtotal", "tip", "gst",
                  "pst", "hst", "total", "payment_method", "category", "line_items",
                  "confidence", "confidence_reason", "source_snippet", "model")


def reparse_failed(family_id: str) -> tuple[int, int]:
    """Re-run the AI on receipts whose initial parse failed (e.g. the API was out of credits),
    using the already-stored image — no re-upload needed. Keeps id/status/hash. Returns
    (fixed, still_failing)."""
    records = load_receipts(family_id)
    fixed = still = 0
    for r in records:
        if not str(r.get("notes") or "").startswith("AI parse failed"):
            continue
        p = upload_path(family_id, r.get("id"))
        if not (p and p.exists()):
            continue
        try:
            raw = parse_receipt(p, family_id)
        except ParseError as e:
            r["notes"] = f"AI parse failed: {e}"
            still += 1
            continue
        fresh = _stamp(raw, r["id"], r.get("source_file") or p.name, r.get("stored_ext") or p.suffix)
        for k in _PARSED_FIELDS:
            r[k] = fresh[k]
        r["notes"] = None
        fixed += 1
    if fixed or still:
        _write_receipts(family_id, records)
    return fixed, still


def backfill_confidence_reasons(family_id: str) -> int:
    """For medium/low receipts uploaded before the 'why' explanation existed, re-read the stored
    image and fill just `confidence` + `confidence_reason` — preserving the user's field edits.
    Returns the count updated."""
    records = load_receipts(family_id)
    updated = 0
    for r in records:
        if r.get("confidence_reason") or r.get("review_status") == "rejected":
            continue
        if r.get("confidence") not in ("medium", "low"):
            continue
        p = upload_path(family_id, r.get("id"))
        if not (p and p.exists()):
            continue
        try:
            raw = parse_receipt(p, family_id)
        except ParseError:
            continue
        r["confidence"] = raw.get("confidence") or r.get("confidence")
        r["confidence_reason"] = raw.get("confidence_reason") or ""
        updated += 1
    if updated:
        _write_receipts(family_id, records)
    return updated


# Fields the client may edit; everything else is program-owned.
_EDITABLE = {
    "kind", "direction", "vendor", "date", "currency", "subtotal", "tip",
    "gst", "pst", "hst", "total", "payment_method", "category", "line_items",
    "review_status", "notes",
}
_MONEY = {"subtotal", "tip", "gst", "pst", "hst", "total"}


def update_receipt(family_id: str, rid: str, patch: dict) -> dict | None:
    """Apply an edit/approve/reject to one record. Returns the updated record, or None."""
    records = load_receipts(family_id)
    found = None
    for r in records:
        if r.get("id") == rid:
            for k, v in (patch or {}).items():
                if k not in _EDITABLE:
                    continue
                if k in _MONEY:
                    v = float(v) if isinstance(v, (int, float)) else (
                        float(v) if isinstance(v, str) and v.strip() not in ("", "-", ".") else 0.0)
                if k == "review_status" and v not in ("pending", "approved", "rejected"):
                    continue
                r[k] = v
                if k in ("hst", "gst", "pst", "subtotal"):
                    r["hst_estimated"] = False     # user is now the source of truth for tax
            found = r
            break
    if found is None:
        return None
    _write_receipts(family_id, records)
    return found


def delete_receipt(family_id: str, rid: str) -> bool:
    """Remove a record and its stored file. Returns True if it existed."""
    records = load_receipts(family_id)
    keep = [r for r in records if r.get("id") != rid]
    if len(keep) == len(records):
        return False
    _write_receipts(family_id, keep)
    p = upload_path(family_id, rid)
    if p:
        p.unlink(missing_ok=True)
    return True


def upload_path(family_id: str, rid: str) -> Path | None:
    """The stored original file for a record id (for serving the thumbnail)."""
    d = uploads_dir(family_id)
    if not d.exists():
        return None
    for p in d.glob(f"{rid}.*"):
        return p
    return None
