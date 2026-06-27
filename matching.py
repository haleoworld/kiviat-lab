#!/usr/bin/env python3
"""Receipt ↔ transaction coverage + matching (Phase 3).

For each transaction that should have a backing document — an **expense** (→ a receipt) or
**income** (→ the invoice you issued), not marked "No receipt" — we either honor a **confirmed
link** (`matched_receipt_id`), or **suggest** the best receipt by amount (±$0.02) and date
(±5 days), vendor↔description similarity breaking ties.

Statuses: linked (confirmed, locked, auto-reconciled) · suggested (candidate, unconfirmed) ·
missing (no candidate) · no_receipt (marked). Confirming a link stores it on both sides and
marks the transaction reconciled; unlinking reverses it.
"""
from __future__ import annotations

import datetime

import business
import statements

AMOUNT_TOL = 0.02
DATE_WINDOW = 5


def _date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _days_apart(a, b) -> int:
    da, db = _date(a), _date(b)
    return abs((da - db).days) if da and db else 999


def _score(r: dict, t: dict) -> int:
    dd = _days_apart(r.get("date"), t.get("date"))
    vendor = str(r.get("vendor") or "").lower().replace(".", " ").replace("(", " ").replace(")", " ")
    desc = str(t.get("description") or "").lower()
    overlap = sum(1 for tok in vendor.split() if len(tok) >= 3 and tok in desc)
    return dd - overlap * 2


def _matchdict(r: dict) -> dict:
    return {"id": r["id"], "vendor": r.get("vendor"), "total": r.get("total"),
            "date": r.get("date"), "stored_ext": r.get("stored_ext")}


def _amount_date_ok(r, t) -> bool:
    return (abs(float(r.get("total") or 0) - abs(float(t.get("amount") or 0))) <= AMOUNT_TOL
            and _days_apart(r.get("date"), t.get("date")) <= DATE_WINDOW)


def coverage(family_id: str) -> dict:
    cfg = business.load_business_config(family_id)
    receipts = [r for r in business.load_receipts(family_id) if r.get("review_status") != "rejected"]
    rec_by_id = {r["id"]: r for r in receipts}
    txns = [t for t in statements.load_transactions(family_id) if t.get("review_status") != "rejected"]
    exp_pool = [r for r in receipts if r.get("direction") != "income"]
    inc_pool = [r for r in receipts if r.get("direction") == "income"]

    need = [t for t in txns if t.get("direction") in ("expense", "income") and not t.get("no_receipt")]
    assigned: set[str] = set()

    # Pass 1: honor confirmed links first (reserve those receipts).
    linked = {}
    for t in need:
        rid = t.get("matched_receipt_id")
        if rid and rid in rec_by_id and rid not in assigned:
            assigned.add(rid)
            linked[t["id"]] = rec_by_id[rid]

    # Pass 2: suggest for the rest from unassigned receipts (oldest-first for stable greedy).
    rows = []
    for t in sorted(need, key=lambda t: t.get("date") or ""):
        if t["id"] in linked:
            rows.append(_txn_row(t, cfg, "linked", _matchdict(linked[t["id"]])))
            continue
        pool = inc_pool if t.get("direction") == "income" else exp_pool
        cands = [r for r in pool if r["id"] not in assigned and _amount_date_ok(r, t)]
        best = min(cands, key=lambda r: _score(r, t)) if cands else None
        if best:
            assigned.add(best["id"])      # reserve so it isn't suggested twice
            rows.append(_txn_row(t, cfg, "suggested", _matchdict(best)))
        else:
            rows.append(_txn_row(t, cfg, "missing", None))

    for t in txns:
        if t.get("direction") in ("expense", "income") and t.get("no_receipt"):
            rows.append(_txn_row(t, cfg, "no_receipt", None))

    orphans = [{"id": r["id"], "vendor": r.get("vendor"), "total": r.get("total"),
                "date": r.get("date"), "direction": r.get("direction"), "category": r.get("category"),
                "fiscal_year": business.fiscal_year_of(r.get("date"), cfg), "stored_ext": r.get("stored_ext")}
               for r in receipts if r["id"] not in assigned]

    need_rows = [x for x in rows if x["status"] in ("linked", "suggested", "missing")]
    def amt(rs): return round(sum(abs(float(x["amount"] or 0)) for x in rs), 2)
    summary = {
        "need": len(need_rows),
        "linked": sum(1 for x in need_rows if x["status"] == "linked"),
        "suggested": sum(1 for x in need_rows if x["status"] == "suggested"),
        "missing": sum(1 for x in need_rows if x["status"] == "missing"),
        "no_receipt": sum(1 for x in rows if x["status"] == "no_receipt"),
        "uncategorized": sum(1 for x in rows if not x.get("category")),
        "orphans": len(orphans),
        "amt_total": amt(need_rows),
        "amt_linked": amt([x for x in need_rows if x["status"] == "linked"]),
        "amt_suggested": amt([x for x in need_rows if x["status"] == "suggested"]),
    }
    fys = sorted({x["fiscal_year"] for x in rows if x.get("fiscal_year") is not None}, reverse=True)
    return {"rows": rows, "orphans": orphans, "summary": summary,
            "accounts": statements.active_accounts(cfg), "fiscal_years": fys,
            "categories": business.ALL_CATEGORIES}


def _txn_row(t: dict, cfg: dict, status: str, match) -> dict:
    return {"id": t["id"], "account": t.get("account"), "date": t.get("date"),
            "amount": t.get("amount"), "direction": t.get("direction"),
            "description": t.get("description"), "category": t.get("category") or "",
            "fiscal_year": business.fiscal_year_of(t.get("date"), cfg),
            "reconciled": bool(t.get("reconciled")), "status": status, "match": match}


def candidates_for_txn(family_id: str, txn_id: str, limit: int = 15) -> list[dict]:
    """Receipts available to manually link to a transaction — those not already linked to another
    transaction — ranked by amount closeness then date proximity. For the manual-link picker."""
    txns = statements.load_transactions(family_id)
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return []
    taken = {x.get("matched_receipt_id") for x in txns if x.get("matched_receipt_id") and x["id"] != txn_id}
    want_income = t.get("direction") == "income"
    amt = abs(float(t.get("amount") or 0))
    pool = [r for r in business.load_receipts(family_id)
            if r.get("review_status") != "rejected" and r["id"] not in taken
            and (r.get("direction") == "income") == want_income]
    pool.sort(key=lambda r: (abs(float(r.get("total") or 0) - amt), _days_apart(r.get("date"), t.get("date"))))
    return [{**_matchdict(r), "category": r.get("category"),
             "amount_diff": round(abs(float(r.get("total") or 0) - amt), 2)} for r in pool[:limit]]


def link_receipt(family_id: str, txn_id: str, receipt_id: str) -> dict | None:
    """Confirm a receipt↔transaction link: store it on both sides, mark the transaction reconciled.
    Frees any prior link on either side (a receipt backs exactly one transaction)."""
    txns = statements.load_transactions(family_id)
    recs = business.load_receipts(family_id)
    t = next((x for x in txns if x.get("id") == txn_id), None)
    r = next((x for x in recs if x.get("id") == receipt_id), None)
    if not t or not r:
        return None
    old_rid = t.get("matched_receipt_id")
    for x in txns:                                   # free any other txn pointing at this receipt
        if x.get("matched_receipt_id") == receipt_id and x["id"] != txn_id:
            x["matched_receipt_id"], x["reconciled"] = None, False
    for rr in recs:
        if rr["id"] == old_rid:
            rr["matched_txn_id"] = None
        if rr["id"] == receipt_id:
            rr["matched_txn_id"] = txn_id
    t["matched_receipt_id"], t["reconciled"] = receipt_id, True
    if not t.get("category") and r.get("category"):   # inherit the receipt's bucket, never overwrite a manual one
        t["category"] = r["category"]
    statements._write_jsonl(statements.statements_path(family_id), txns)
    business._write_receipts(family_id, recs)
    return t


def unlink_receipt(family_id: str, txn_id: str) -> dict | None:
    txns = statements.load_transactions(family_id)
    recs = business.load_receipts(family_id)
    t = next((x for x in txns if x.get("id") == txn_id), None)
    if not t:
        return None
    rid = t.get("matched_receipt_id")
    t["matched_receipt_id"], t["reconciled"] = None, False
    for rr in recs:
        if rr["id"] == rid:
            rr["matched_txn_id"] = None
    statements._write_jsonl(statements.statements_path(family_id), txns)
    business._write_receipts(family_id, recs)
    return t
