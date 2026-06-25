#!/usr/bin/env python3
"""Phase 2 of the receipts → corp-tax pipeline: statement import.

Pull each account's transactions in from ANY of four formats into one canonical ledger:

    CSV / Excel  → AI detects the column mapping once, then code parses every row
                   deterministically (exact amounts, no dropped rows).
    PDF / image  → AI extracts the rows directly (the only option for pixels).

A completeness gate (opening + Σamount ≈ closing, + row count) flags anything an AI
read might have dropped, before you commit. Sign convention: negative = money out /
charge / expense; positive = money in / refund / payment received. Card "PAYMENT THANK
YOU" and inter-account moves are tagged `transfer`, not spend.

Storage, per family, under `business/`:
    statements_files/<import_id>.<ext>   original uploaded file (audit trail)
    imports.jsonl                        one record per uploaded file (account, balances,
                                         parsed vs stated count, reconciled flag, status)
    statements.jsonl                     canonical transactions (matched_receipt_id=null
                                         until Phase 3)

Like receipts, nothing is trusted until reviewed: an import lands as `pending`; you
fix/confirm, then Commit. This module never sys.exit / extract.die in the request path.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import business           # ParseError, ACCOUNT helpers, shared conventions
import config
import extract
import paths

ParseError = business.ParseError   # reuse the same recoverable-error type

# The five accounts the accountant reconciles (canonical names). User picks one per import.
ACCOUNTS = ["RBC Bank", "TD Bank", "Amex 1001", "Amex 1002", "Rogers Credit Card"]
CREDIT_CARD_ACCOUNTS = {"Amex 1001", "Amex 1002", "Rogers Credit Card"}


def account_list(cfg: dict | None = None) -> list[str]:
    """All account names configured for this family. A family with a custom 'accounts' key
    uses it (even when empty — a fresh business starts with none); legacy families that
    predate per-family accounts fall back to the historical built-in list so existing books
    keep working with no migration."""
    cfg = cfg or {}
    return list(cfg["accounts"]) if "accounts" in cfg else list(ACCOUNTS)


def credit_card_accounts(cfg: dict | None = None) -> set[str]:
    """Which of this family's accounts are credit cards (drives parsing/HST wording)."""
    cfg = cfg or {}
    if "accounts" in cfg:
        return set(cfg.get("credit_card_accounts", []))
    return set(CREDIT_CARD_ACCOUNTS)


def account_kind(account: str, cfg: dict | None = None) -> str:
    return "credit card" if account in credit_card_accounts(cfg) else "bank account"


def active_accounts(cfg: dict | None = None) -> list[str]:
    """Accounts offered for new imports — the family's list minus any archived (e.g. a
    cancelled card). Archived accounts stay valid for historical data, just off the picker."""
    cfg = cfg or {}
    archived = set(cfg.get("archived_accounts", []))
    return [a for a in account_list(cfg) if a not in archived]

TABULAR_SUFFIXES = {".csv", ".xlsx"}
IMAGE_SUFFIXES = set(extract.IMAGE_MEDIA_TYPES) | {".heic"}
DOC_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES
SUPPORTED_SUFFIXES = TABULAR_SUFFIXES | DOC_SUFFIXES
RECONCILE_TOLERANCE = 0.02


# ---------- layout ----------

def files_dir(family_id: str) -> Path:
    return business.business_dir(family_id) / "statements_files"


def imports_path(family_id: str) -> Path:
    return business.business_dir(family_id) / "imports.jsonl"


def statements_path(family_id: str) -> Path:
    return business.business_dir(family_id) / "statements.jsonl"


def ensure_dirs(family_id: str) -> None:
    files_dir(family_id).mkdir(parents=True, exist_ok=True)


# ---------- jsonl helpers ----------

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def load_imports(family_id: str) -> list[dict]:
    out = _load_jsonl(imports_path(family_id))
    out.sort(key=lambda r: r.get("imported_at") or "", reverse=True)
    return out


def load_transactions(family_id: str) -> list[dict]:
    return _load_jsonl(statements_path(family_id))


# ---------- dedup + reconcile ----------

def dedup_key(account: str, date: str, amount, description: str, balance=None) -> str:
    """Identity of a transaction for cross-import dedup: (account, date, amount, normalized
    description, running balance). The balance is what distinguishes two otherwise-identical
    same-day transactions (e.g. two equal Procom deposits) — without it they'd collapse."""
    norm = " ".join((description or "").lower().split())
    amt = f"{float(amount or 0):.2f}"
    bal = "" if balance is None else f"{float(balance):.2f}"
    raw = f"{account}|{date or ''}|{amt}|{norm}|{bal}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _chain_check(txns_in_order: list[dict]) -> dict:
    """Completeness check for sources that carry a running balance (screenshots, many CSVs):
    in display order (newest→oldest) each row's balance minus its amount should equal the next
    row's balance. A break means a row is likely missing between them. Returns {ok, breaks}."""
    have = sum(1 for t in txns_in_order if isinstance(t.get("balance"), (int, float)))
    if have < 2:
        return {"balance_chain_ok": None, "chain_breaks": None}

    def breaks_for(rows):
        # Assumes `rows` are newest→oldest: each row's balance minus its amount = next row's balance.
        n = 0
        for a, b in zip(rows, rows[1:]):
            ba, aa, bb = a.get("balance"), a.get("amount"), b.get("balance")
            if not isinstance(ba, (int, float)) or not isinstance(bb, (int, float)):
                continue
            if abs(round(float(ba) - float(aa or 0) - float(bb), 2)) > RECONCILE_TOLERANCE:
                n += 1
        return n

    # Order-agnostic: screenshots are newest-first, CSV/Excel often oldest-first. A valid
    # running-balance sequence is consistent in exactly one direction — take the better.
    breaks = min(breaks_for(txns_in_order), breaks_for(list(reversed(txns_in_order))))
    return {"balance_chain_ok": breaks == 0, "chain_breaks": breaks}


def _reconcile(opening, closing, txns: list[dict]) -> dict:
    """Compute the completeness check. Reconciled only when balances are present AND
    opening + Σamount == closing within tolerance."""
    total = round(sum(float(t.get("amount") or 0) for t in txns), 2)
    out = {"parsed_count": len(txns), "sum_amount": total,
           "opening_balance": opening, "closing_balance": closing, "reconciled": None,
           "discrepancy": None}
    if isinstance(opening, (int, float)) and isinstance(closing, (int, float)):
        # Sign-agnostic: a bank/asset balance RISES with +amount (closing = opening + Σ); a
        # credit-card/liability balance owed FALLS with +amount payments (closing = opening − Σ).
        # A real statement reconciles in exactly one direction — take the smaller discrepancy.
        d_asset = round(float(opening) + total - float(closing), 2)
        d_liab = round(float(opening) - total - float(closing), 2)
        disc = min((d_asset, d_liab), key=abs)
        out["discrepancy"] = disc
        out["reconciled"] = abs(disc) <= RECONCILE_TOLERANCE
    return out


# ---------- canonical record ----------

def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("$", "").strip()
        # (123.45) accounting-negative
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            return -float(s) if neg else float(s)
        except ValueError:
            return 0.0
    return 0.0


def _txn(raw: dict, import_id: str, account: str, source: str) -> dict:
    """Force the canonical transaction shape; program stamps id/import/status."""
    amount = round(_num(raw.get("amount")), 2)
    direction = raw.get("direction")
    if direction not in ("expense", "income", "transfer"):
        direction = "income" if amount > 0 else "expense"
    date = (raw.get("date") or "").strip()
    desc = (raw.get("description") or "").strip()
    balance = raw.get("balance")
    balance = round(float(balance), 2) if isinstance(balance, (int, float)) else None
    return {
        "id": str(uuid.uuid4()),
        "import_id": import_id,
        "account": account,
        "date": date,
        "amount": amount,                       # signed: negative = money out
        "balance": balance,                     # running balance after this txn (if shown)
        "direction": direction,                 # expense | income | transfer
        "description": desc,
        "category": "",                         # filled in Phase 4 / inherited via match
        "matched_receipt_id": None,             # Phase 3
        "no_receipt": False,                    # user-marked: this txn legitimately has no receipt
        "reconciled": False,                    # user-marked: accounted for / verified
        "review_status": "pending",             # pending | committed | rejected
        "source": source,                       # csv | xlsx | pdf | image
        "dedup_key": dedup_key(account, date, amount, desc, balance),
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


# ---------- tabular reader (CSV / XLSX): AI maps columns, code parses rows ----------

_COLUMN_TOOL = {
    "name": "map_statement_columns",
    "description": "Given a bank/credit-card statement's header row and a few sample rows, "
                   "identify which columns hold the date, description, and amount.",
    "input_schema": {
        "type": "object",
        "properties": {
            "date_col": {"type": "string", "description": "Exact header name of the transaction-date column."},
            "description_col": {"type": "string", "description": "Exact header name of the description/merchant column."},
            "amount_mode": {"type": "string", "enum": ["single", "debit_credit"],
                            "description": "'single' = one signed amount column; 'debit_credit' = separate debit (money out) and credit (money in) columns."},
            "amount_col": {"type": "string", "description": "Header of the signed amount column (when amount_mode='single'). Empty otherwise."},
            "debit_col": {"type": "string", "description": "Header of the debit/withdrawal column (money out), when amount_mode='debit_credit'. Empty otherwise."},
            "credit_col": {"type": "string", "description": "Header of the credit/deposit column (money in), when amount_mode='debit_credit'. Empty otherwise."},
            "balance_col": {"type": "string", "description": "Header of the running-balance column if present, else empty."},
            "single_sign": {"type": "string", "enum": ["out_negative", "out_positive"],
                            "description": "For amount_mode='single': is money OUT shown as a negative number ('out_negative', typical bank export) or a positive number ('out_positive', some credit-card exports where charges are positive)?"},
            "date_format": {"type": "string", "description": "strftime pattern for the date column, e.g. '%Y-%m-%d', '%m/%d/%Y', '%d %b %Y'."},
            "opening_balance": {"type": ["number", "null"], "description": "Opening/previous balance if stated anywhere in the sheet, else null."},
            "closing_balance": {"type": ["number", "null"], "description": "Closing/ending balance if stated, else null."},
            "header_row_index": {"type": "integer", "description": "0-based index of the header row within the provided rows (statements sometimes have title rows above the header)."},
        },
        "required": ["date_col", "description_col", "amount_mode", "single_sign", "date_format", "header_row_index"],
    },
}


def _read_tabular_rows(path: Path) -> list[list[str]]:
    """Return the sheet as a list of string rows (CSV or XLSX)."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        text = path.read_text(encoding="utf-8-sig", errors="replace")   # strip any BOM
        return [row for row in csv.reader(io.StringIO(text))]
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            raise ParseError("openpyxl not installed on the server (needed for .xlsx).", 500)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else str(c) for c in r])
        wb.close()
        return rows
    raise ParseError(f"Not a tabular file: {suffix}", 400)


def _parse_date(value: str, fmt: str) -> str:
    """Best-effort date → ISO YYYY-MM-DD. Falls back to a few common formats."""
    value = (value or "").strip()
    if not value:
        return ""
    for f in [fmt, "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(value, f).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return value  # keep raw; the review screen will show it for a human to fix


def read_tabular(path: Path, family_id: str) -> tuple[list[dict], dict]:
    """CSV/XLSX → (raw transaction dicts, reconcile-meta). AI maps columns once; code parses all rows."""
    rows = _read_tabular_rows(path)
    if not rows:
        raise ParseError("The file appears to be empty.", 400)

    sample = rows[:8]
    api_key = config.load_api_key()
    if not api_key:
        raise ParseError("No ANTHROPIC_API_KEY set in the data-root .env.", 400)
    from anthropic import Anthropic
    model = config.load_app_config()["extraction"]["model"]
    prompt = ("Identify the columns of this bank/credit-card statement export so a program can "
              "parse every row. Money OUT must end up negative. Here are the first rows "
              "(as CSV):\n\n" + "\n".join(",".join(r) for r in sample))
    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model, max_tokens=700, tools=[_COLUMN_TOOL],
            tool_choice={"type": "tool", "name": "map_statement_columns"},
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        raise ParseError(f"Claude column-detection failed: {e}", 502)
    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        raise ParseError("Claude did not return a column mapping.", 502)
    m = dict(block.input)

    hdr_idx = int(m.get("header_row_index") or 0)
    header = rows[hdr_idx]
    # Normalize header keys (strip stray BOM/whitespace) so a column like "﻿Posted Date"
    # still matches the AI's "Posted Date".
    norm = lambda s: (s or "").strip().lstrip("﻿")
    col = {norm(name): i for i, name in enumerate(header)}

    def colidx(key):
        return col.get(norm(m.get(key)))

    di, desci = colidx("date_col"), colidx("description_col")
    debit_i, credit_i, amt_i, bal_i = (colidx("debit_col"), colidx("credit_col"),
                                       colidx("amount_col"), colidx("balance_col"))
    mode = m.get("amount_mode")
    fmt = m.get("date_format") or "%Y-%m-%d"

    txns = []
    for row in rows[hdr_idx + 1:]:
        if not any((c or "").strip() for c in row):
            continue
        def cell(i):
            return row[i] if (i is not None and 0 <= i < len(row)) else ""
        date = _parse_date(cell(di), fmt)
        desc = cell(desci)
        if mode == "debit_credit":
            amount = round(_num(cell(credit_i)) - abs(_num(cell(debit_i))), 2)  # in +, out −
        else:
            amt = _num(cell(amt_i))
            amount = amt if m.get("single_sign") == "out_negative" else -amt
        if not date and not desc and amount == 0:
            continue
        bal_cell = cell(bal_i)
        balance = _num(bal_cell) if (bal_cell or "").strip() else None
        txns.append({"date": date, "description": desc, "amount": amount, "balance": balance})

    meta = _reconcile(m.get("opening_balance"), m.get("closing_balance"), txns)
    meta.update(_chain_check(txns))
    meta["column_map"] = m
    return txns, meta


# ---------- AI reader (PDF / image) ----------

_TXN_TOOL = {
    "name": "extract_statement",
    "description": "Extract every transaction line from this bank/credit-card statement "
                   "(PDF or screenshot), plus the opening and closing balances.",
    "input_schema": {
        "type": "object",
        "properties": {
            "transactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD."},
                        "description": {"type": "string"},
                        "amount": {"type": "number", "description": "Signed: NEGATIVE = money out / charge / purchase; POSITIVE = money in / payment / refund / deposit."},
                        "balance": {"type": ["number", "null"], "description": "Running account balance shown AFTER this transaction (often a smaller gray number under the amount). null if not shown."},
                        "direction": {"type": "string", "enum": ["expense", "income", "transfer"],
                                      "description": "'transfer' for credit-card payments ('PAYMENT THANK YOU') and inter-account moves; 'expense' for purchases; 'income' for deposits/sales received."},
                    },
                    "required": ["date", "description", "amount", "direction"],
                },
            },
            "opening_balance": {"type": ["number", "null"], "description": "Opening/previous balance if shown, else null."},
            "closing_balance": {"type": ["number", "null"], "description": "Closing/new balance if shown, else null."},
            "stated_count": {"type": ["integer", "null"], "description": "Transaction count if the statement states one, else null."},
        },
        "required": ["transactions"],
    },
}

def _txn_prompt(account: str, kind: str) -> str:
    if kind == "credit card":
        sign = (
            "SIGN RULE (credit card) — output the OPPOSITE of the sign PRINTED on the statement. "
            "The printed sign tells you the TYPE, not the merchant:\n"
            "- A purchase/charge prints as POSITIVE → output NEGATIVE (money spent), direction 'expense'.\n"
            "- A refund/credit/return prints as NEGATIVE → output POSITIVE (money back), direction 'expense' "
            "(a positive contra amount that offsets the expense).\n"
            "- A card payment ('PAYMENT RECEIVED - THANK YOU', usually printed negative) → output POSITIVE, "
            "direction 'transfer'.\n"
            "CRUCIAL: a NEGATIVE printed amount is ALWAYS a refund/credit, even from a merchant you also "
            "buy from — e.g. 'AMAZON.CA  -395.44' is a $395.44 REFUND → output +395.44, NOT a -395.44 purchase. "
            "Never decide the sign from the merchant name; decide it from the printed sign."
        )
    else:
        sign = (
            "SIGN RULE (bank account) — money OUT (purchases, withdrawals, fees, transfers out) is "
            "NEGATIVE; money IN (deposits, refunds, payments received) is POSITIVE. Bank statements "
            "already print withdrawals as negative, so use the sign as printed. Tag card payments and "
            "inter-account transfers as direction 'transfer'; purchases as 'expense'; deposits as 'income'."
        )
    return (
        f"This is a statement for the account '{account}' (a {kind}). "
        "Extract EVERY transaction row — do not skip, summarize, or merge any.\n\n" + sign + "\n\n"
        "For EACH row, capture the running balance shown after it if present. Two rows with the same "
        "date, amount, and description are SEPARATE transactions; include both. Report the opening "
        "(previous) and closing (new) balances exactly as printed. If a value is unclear, include the "
        "row with your best read rather than dropping it."
    )


def read_document(path: Path, family_id: str, account: str = "") -> tuple[list[dict], dict]:
    """PDF/image → (raw transaction dicts, reconcile-meta) via Claude tool-use."""
    api_key = config.load_api_key()
    if not api_key:
        raise ParseError("No ANTHROPIC_API_KEY set in the data-root .env.", 400)
    from anthropic import Anthropic
    content_block = extract.build_content_block(path)   # suffix pre-validated by caller
    model = config.load_app_config()["extraction"]["model"]
    max_tokens = config.load_app_config()["extraction"]["max_tokens"]
    prompt = _txn_prompt(account or "this account",
                         account_kind(account, business.load_business_config(family_id)))
    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, tools=[_TXN_TOOL],
            tool_choice={"type": "tool", "name": "extract_statement"},
            messages=[{"role": "user", "content": [content_block, {"type": "text", "text": prompt}]}])
    except Exception as e:
        raise ParseError(f"Claude statement extraction failed: {e}", 502)
    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        raise ParseError("Claude did not return statement transactions.", 502)
    out = dict(block.input)
    txns = out.get("transactions") if isinstance(out.get("transactions"), list) else []
    meta = _reconcile(out.get("opening_balance"), out.get("closing_balance"), txns)
    meta.update(_chain_check(txns))
    meta["stated_count"] = out.get("stated_count")
    if isinstance(meta["stated_count"], int):
        meta["count_ok"] = meta["stated_count"] == len(txns)
    return txns, meta


# ---------- ingest orchestration ----------

def _convert_heic(src: Path) -> Path:
    return business._convert_heic(src)   # reuse the receipts HEIC→JPEG helper


def ingest_statement(family_id: str, account: str, filename: str, data: bytes) -> dict:
    """Save one uploaded statement file, parse it by type, dedup, and stage the import.

    Returns an `import` record carrying its `transactions` (pending) and reconcile meta.
    Raises ParseError on an unusable upload; parser failures bubble up as ParseError too
    (the whole batch is one file, so there's no half-good state to salvage).
    """
    ensure_dirs(family_id)
    accts = account_list(business.load_business_config(family_id))
    if account not in accts:
        valid = ", ".join(accts) or "(none configured — add one in Settings)"
        raise ParseError(f"Unknown account '{account}'. Pick one of: {valid}.", 400)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"Unsupported file type '{suffix or '(none)'}' — use CSV, XLSX, PDF, JPG, PNG, or HEIC.", 400)

    import_id = str(uuid.uuid4())
    stored = files_dir(family_id) / f"{import_id}{suffix}"
    stored.write_bytes(data)
    source = {".csv": "csv", ".xlsx": "xlsx", ".pdf": "pdf"}.get(suffix, "image")
    try:
        if suffix == ".heic":
            stored = _convert_heic(stored)
        if stored.suffix.lower() == ".pdf":
            stored = business.repair_pdf(stored)   # fix Fiserv-style junk-prefixed PDFs
        if suffix in TABULAR_SUFFIXES:
            raw_txns, meta = read_tabular(stored, family_id)
        else:
            raw_txns, meta = read_document(stored, family_id, account)
    except ParseError:
        stored.unlink(missing_ok=True)   # don't orphan the stored file on a parse failure
        raise

    # Build canonical txns. Dedup ONLY against PRIOR imports (re-uploading an overlapping
    # statement), never within this batch — the bank listing two identical lines means two
    # real transactions (the running balance, now in the key, keeps them distinct on re-import).
    seen = {t.get("dedup_key") for t in load_transactions(family_id)}
    txns, dup_count = [], 0
    for raw in raw_txns:
        t = _txn(raw, import_id, account, source)
        if t["dedup_key"] in seen:
            dup_count += 1
            continue
        txns.append(t)

    rec = {
        "id": import_id,
        "account": account,
        "source": source,
        "source_file": filename or stored.name,
        "stored_ext": stored.suffix.lower(),
        "status": "pending",                  # pending | committed
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "duplicates_skipped": dup_count,
        **meta,
        "parsed_count": len(txns),            # after dedup
    }
    # Persist: append pending txns to the ledger + the import record.
    all_txns = load_transactions(family_id) + txns
    _write_jsonl(statements_path(family_id), all_txns)
    imports = _load_jsonl(imports_path(family_id))
    imports.append(rec)
    _write_jsonl(imports_path(family_id), imports)

    # FY summary on the returned record so the UI's filter/badges update immediately.
    cfg = business.load_business_config(family_id)
    fys = sorted({business.fiscal_year_of(t["date"], cfg) for t in txns if t.get("date")} - {None})
    rec["fiscal_years"] = fys
    rec["cross_fy"] = len(fys) > 1
    rec["transactions"] = txns
    return rec


# ---------- review / commit ----------

_EDITABLE = {"date", "amount", "description", "direction", "review_status",
             "no_receipt", "reconciled"}


def update_transaction(family_id: str, tid: str, patch: dict) -> dict | None:
    txns = load_transactions(family_id)
    found = None
    for t in txns:
        if t.get("id") == tid:
            for k, v in (patch or {}).items():
                if k not in _EDITABLE:
                    continue
                if k == "amount":
                    v = round(_num(v), 2)
                if k == "direction" and v not in ("expense", "income", "transfer"):
                    continue
                if k == "review_status" and v not in ("pending", "committed", "rejected"):
                    continue
                if k in ("no_receipt", "reconciled"):
                    v = bool(v)
                t[k] = v
            # keep dedup_key in sync if identity fields changed
            t["dedup_key"] = dedup_key(t["account"], t["date"], t["amount"], t["description"])
            found = t
            break
    if found is None:
        return None
    _write_jsonl(statements_path(family_id), txns)
    return found


def set_import_status(family_id: str, import_id: str, status: str) -> dict | None:
    """Commit or reject a whole import: flips its txns and the import record together."""
    if status not in ("committed", "rejected"):
        return None
    imports = _load_jsonl(imports_path(family_id))
    rec = next((r for r in imports if r.get("id") == import_id), None)
    if rec is None:
        return None
    rec["status"] = status
    _write_jsonl(imports_path(family_id), imports)

    txns = load_transactions(family_id)
    if status == "rejected":
        kept = [t for t in txns if t.get("import_id") != import_id]
        _write_jsonl(statements_path(family_id), kept)
    else:
        for t in txns:
            if t.get("import_id") == import_id and t.get("review_status") == "pending":
                t["review_status"] = "committed"
        _write_jsonl(statements_path(family_id), txns)
    return rec


def delete_import(family_id: str, import_id: str) -> bool:
    """Remove an import, its transactions, and its stored file."""
    imports = _load_jsonl(imports_path(family_id))
    keep = [r for r in imports if r.get("id") != import_id]
    if len(keep) == len(imports):
        return False
    _write_jsonl(imports_path(family_id), keep)
    txns = [t for t in load_transactions(family_id) if t.get("import_id") != import_id]
    _write_jsonl(statements_path(family_id), txns)
    p = import_file_path(family_id, import_id)
    if p:
        p.unlink(missing_ok=True)
    return True


def import_file_path(family_id: str, import_id: str) -> Path | None:
    d = files_dir(family_id)
    if not d.exists():
        return None
    for p in d.glob(f"{import_id}.*"):
        return p
    return None


def overview(family_id: str) -> dict:
    """Imports (with their transactions nested) + accounts + fiscal-year labelling, for the UI."""
    cfg = business.load_business_config(family_id)
    imports = load_imports(family_id)
    txns = load_transactions(family_id)
    by_import: dict[str, list] = {}
    all_fys: set = set()
    for t in txns:
        fy = business.fiscal_year_of(t.get("date"), cfg)
        t["fiscal_year"] = fy                       # computed, not stored → reflows if config changes
        if fy is not None:
            all_fys.add(fy)
        by_import.setdefault(t.get("import_id"), []).append(t)
    for r in imports:
        rt = sorted(by_import.get(r["id"], []), key=lambda t: t.get("date") or "")
        r["transactions"] = rt
        fys = sorted({t["fiscal_year"] for t in rt if t.get("fiscal_year") is not None})
        r["fiscal_years"] = fys
        r["cross_fy"] = len(fys) > 1
    fy_bounds = {fy: business.fiscal_year_bounds(fy, cfg) for fy in all_fys}
    return {"imports": imports, "accounts": active_accounts(cfg), "all_accounts": account_list(cfg),
            "archived_accounts": cfg.get("archived_accounts", []), "config": cfg,
            "fiscal_years": sorted(all_fys, reverse=True), "fiscal_year_bounds": fy_bounds}
