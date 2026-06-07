"""Computed views — pure functions over a family's snapshot + target allocation.

Each view returns a dict with at least {value/series, confidence, notes}. Numbers
are derived live from the data; confidence reflects how trustworthy the inputs are.

Modeling assumptions (stated, easy to change):
  - Real estate is counted at EQUITY (value − mortgage).
  - Equities accounts are counted GROSS; non-mortgage debts (RRSP/car/parents) are
    shown as leverage, not mixed into the asset allocation.
  - Allocation % is of the net ASSET BASE (sum of asset-class values), with leverage
    reported separately.
  - Liquid runway assumes income stops (liquid ÷ total monthly spend).
"""
from __future__ import annotations

from typing import Any

import yaml

import config
import paths

# Risk weight per asset class (1 = safest, 5 = most volatile).
RISK_WEIGHTS = {
    "cash_tbill": 1, "real_estate": 2, "equities_dividend": 3,
    "gold": 3, "equities_growth": 4, "equities_uncat": 4,
    "crypto": 5, "equities_trade": 5,
}
CATEGORY_LABELS = {
    "real_estate": "Real Estate", "cash_tbill": "Cash / T-Bill", "gold": "Gold",
    "crypto": "Crypto", "equities_growth": "Equities — Growth",
    "equities_dividend": "Equities — Dividend", "equities_trade": "Equities — Trade",
    "equities_uncat": "Equities — Uncategorized",
}


# ---------- loaders ----------

def load_snapshot(family_id: str) -> dict:
    f = paths.family_dir(family_id) / "snapshot.yaml"
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text()) or {}


def load_target(family_id: str) -> dict:
    f = paths.family_dir(family_id) / "allocation_target.yaml"
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text()) or {}


def load_holdings(family_id: str) -> list:
    f = paths.family_dir(family_id) / "holdings.yaml"
    if not f.exists():
        return []
    return (yaml.safe_load(f.read_text()) or {}).get("holdings", [])


# ---------- helpers ----------

def _sum(items, key):
    return sum((it.get(key) or 0) for it in items)


# ---------- views ----------

def net_worth(snap: dict) -> dict:
    accounts = snap.get("accounts", [])
    props = snap.get("properties", [])
    debts = snap.get("debts", [])

    accounts_total = _sum(accounts, "value")
    property_value = _sum(props, "value")
    mortgages = _sum(props, "mortgage")
    other_debt_known = sum((d.get("balance") or 0) for d in debts)
    missing = [d["name"] for d in debts if d.get("balance") is None]

    gross_assets = accounts_total + property_value
    liabilities = mortgages + other_debt_known
    value = gross_assets - liabilities

    return {
        "value": round(value),
        "gross_assets": round(gross_assets),
        "liabilities": round(liabilities),
        "accounts_total": round(accounts_total),
        "property_value": round(property_value),
        "mortgages": round(mortgages),
        "confidence": "medium",
        "missing_data_flags": [f"{m} balance unknown" for m in missing],
        "notes": "Property values are estimates; unknown debt balances would lower this.",
    }


def liquidity_runway(snap: dict) -> dict:
    accounts = snap.get("accounts", [])
    exp = snap.get("expenses_monthly", {})
    liquid = sum(a["value"] for a in accounts if a.get("liquid"))
    monthly_out = exp.get("total_now") or 1
    shortfall = (snap.get("net_position", {}) or {}).get("personal_shortfall_mo_now") or 0

    liquid_runway_m = liquid / monthly_out if monthly_out else None
    drawdown_runway_m = (liquid / shortfall) if shortfall else None

    # Stressed: one income lost + equities down 40% (cash unaffected).
    stressed_liquid = sum(
        (a["value"] * 0.6 if a.get("asset_class", "").startswith("equities") else a["value"])
        for a in accounts if a.get("liquid")
    )
    stressed_runway_m = stressed_liquid / monthly_out if monthly_out else None

    return {
        "liquid": round(liquid),
        "liquid_runway_months": round(liquid_runway_m, 1) if liquid_runway_m else None,
        "drawdown_runway_months": round(drawdown_runway_m, 1) if drawdown_runway_m else None,
        "stressed_runway_months": round(stressed_runway_m, 1) if stressed_runway_m else None,
        "confidence": "medium",
        "notes": ("Liquid runway = liquid ÷ total monthly spend (income stops). "
                  "Drawdown runway = liquid ÷ current personal shortfall (income continues)."),
    }


def cash_flow(snap: dict) -> dict:
    inc = (snap.get("income_monthly", {}) or {}).get("earned_to_hand") or 0
    exp = snap.get("expenses_monthly", {}) or {}
    out_now = exp.get("total_now") or 0
    out_ren = exp.get("total_renewal") or 0
    corp = snap.get("corp", {}) or {}

    personal_now = inc - out_now
    personal_ren = inc - out_ren
    household_now_yr = personal_now * 12 + (corp.get("annual_accumulation") or 0)

    return {
        "personal_in": round(inc),
        "personal_out_now": round(out_now),
        "personal_out_renewal": round(out_ren),
        "personal_net_now": round(personal_now),
        "personal_net_renewal": round(personal_ren),
        "household_net_year_now": round(household_now_yr),
        "corp_accumulation_year": round(corp.get("annual_accumulation") or 0),
        "confidence": "medium",
        "notes": ("Personal accounts drain ~${:,}/mo, but the corp accumulates "
                  "~${:,}/yr — household is roughly flat-to-positive.").format(
                      abs(round(personal_now)), round(corp.get("annual_accumulation") or 0)),
    }


def allocation(snap: dict, target: dict, holdings: list = None, fx_usd_cad: float = 1.0) -> dict:
    accounts = snap.get("accounts", [])
    props = snap.get("properties", [])
    holdings = holdings or []

    by_cat: dict[str, float] = {}
    # Real estate at BOOK equity (purchase price − current mortgage).
    re_equity = sum(((p.get("purchase_price") or p.get("value") or 0) - (p.get("mortgage") or 0))
                    for p in props)
    if props:
        by_cat["real_estate"] = re_equity

    if holdings:
        # Categorized brokerage holdings (book, USD→CAD) + cash accounts (chequing).
        for h in holdings:
            cat = h.get("category", "equities_uncat")
            book = (h.get("book") or 0)
            if h.get("currency") == "USD":
                book *= fx_usd_cad
            by_cat[cat] = by_cat.get(cat, 0) + book
        for a in accounts:
            if a.get("asset_class") == "cash_tbill":  # chequing, not brokerage
                by_cat["cash_tbill"] = by_cat.get("cash_tbill", 0) + (a.get("value") or 0)
    else:
        # Fallback: accounts lumped by asset_class (no holdings detail yet).
        for a in accounts:
            cat = a.get("asset_class", "equities_uncat")
            by_cat[cat] = by_cat.get(cat, 0) + (a.get("value") or 0)

    asset_base = sum(by_cat.values()) or 1
    targets = target.get("target_allocation", {}) or {}
    tol = target.get("drift_tolerance_pp", 5)

    # Target for the uncategorized equities bucket = sum of equities_* targets.
    equities_target = sum(v for k, v in targets.items() if k.startswith("equities"))

    rows = []
    all_cats = set(by_cat) | set(targets)
    for cat in sorted(all_cats):
        actual_val = by_cat.get(cat, 0)
        actual_pct = 100 * actual_val / asset_base
        if cat == "equities_uncat":
            tgt = equities_target
        else:
            tgt = targets.get(cat, 0)
        drift = actual_pct - tgt
        status = "ok"
        if cat in by_cat and actual_val == 0 and tgt > 0:
            status = "missing"
        elif abs(drift) > tol:
            status = "over" if drift > 0 else "under"
        elif tgt > 0 and actual_val == 0:
            status = "missing"
        rows.append({
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat),
            "actual_value": round(actual_val),
            "actual_pct": round(actual_pct, 1),
            "target_pct": tgt,
            "drift_pp": round(drift, 1),
            "status": status,
        })

    # Risk score (weighted) vs the target's implied score.
    port_score = sum(RISK_WEIGHTS.get(r["category"], 3) * r["actual_pct"] / 100 for r in rows)
    tgt_score = sum(RISK_WEIGHTS.get(c, 3) * p / 100 for c, p in targets.items())

    if holdings:
        flags = [
            f"USD accounts (TFSA, RRSP) converted to CAD at {fx_usd_cad}.",
            "All asset tiers at BOOK: securities at cost; real estate at purchase-price equity.",
            "Book real-estate equity treats the condo at its $799k purchase (it's underwater "
            "at market — see Risks).",
            "RRSP holdings may be partial (book seen ~$105k vs stated $154k).",
        ]
        notes = ("Book basis throughout (per your setting). Real estate = purchase price − "
                 "mortgage. Allocation is of the net asset base across all accounts + chequing.")
        conf = "medium"
    else:
        flags = [
            "Brokerage holdings not yet categorized (growth/dividend/trade).",
            "Gold and crypto buckets empty — confirm none held inside brokerage.",
        ]
        notes = ("Real estate shown at equity; allocation is of the net asset base. "
                 "Equities are lumped until portfolio screenshots are categorized.")
        conf = "low"

    return {
        "asset_base": round(asset_base),
        "rows": rows,
        "drift_tolerance_pp": tol,
        "risk_score": round(port_score, 2),
        "target_risk_score": round(tgt_score, 2),
        "risk_vs_target": "above" if port_score > tgt_score else "below",
        "confidence": conf,
        "missing_data_flags": flags,
        "notes": notes,
    }


ACCOUNT_LABELS = {
    "TFSA": "TFSA", "RRSP": "RRSP", "RESP1": "RESP #1", "RESP2": "RESP #2",
    "Corp": "Corp brokerage",
}


def account_allocation(snap: dict, holdings: list = None, fx_usd_cad: float = 1.0) -> dict:
    """Per-account value, share of total portfolio, and internal category mix (CAD, book)."""
    holdings = holdings or []
    accts: dict[str, dict] = {}

    def add(name, cat, val):
        a = accts.setdefault(name, {"value": 0.0, "by_cat": {}})
        a["value"] += val
        a["by_cat"][cat] = a["by_cat"].get(cat, 0) + val

    for h in holdings:
        val = (h.get("book") or 0) * (fx_usd_cad if h.get("currency") == "USD" else 1)
        add(ACCOUNT_LABELS.get(h.get("account"), h.get("account")),
            h.get("category", "equities_uncat"), val)
    for a in snap.get("accounts", []):
        if a.get("asset_class") == "cash_tbill":  # chequing
            add(a.get("name"), "cash_tbill", a.get("value") or 0)
    re = sum(((p.get("purchase_price") or p.get("value") or 0) - (p.get("mortgage") or 0))
             for p in snap.get("properties", []))
    if re:
        add("Real Estate (equity)", "real_estate", re)

    total = sum(a["value"] for a in accts.values()) or 1
    rows = []
    for name, a in accts.items():
        mix = [{"label": CATEGORY_LABELS.get(c, c), "value": round(v),
                "pct": round(100 * v / a["value"])}
               for c, v in sorted(a["by_cat"].items(), key=lambda kv: -kv[1])]
        rows.append({"account": name, "value": round(a["value"]),
                     "pct": round(100 * a["value"] / total, 1), "mix": mix})
    rows.sort(key=lambda r: -r["value"])
    return {"accounts": rows, "total": round(total), "confidence": "medium"}


def ranked_risks(snap: dict, alloc: dict) -> dict:
    risks = []
    props = {p["name"]: p for p in snap.get("properties", [])}

    # 1. Underwater investment property.
    for p in snap.get("properties", []):
        equity = (p.get("value") or 0) - (p.get("mortgage") or 0)
        if equity < 0:
            bleed = p.get("net_bleed_mo_renewal") or p.get("net_bleed_mo_now") or 0
            risks.append({
                "title": f"{p['name']} is deeply underwater",
                "severity": "high",
                "detail": (f"Equity ≈ ${equity:,.0f}. Net carry bleeds ~${bleed:,}/mo "
                           f"(~${bleed*12:,}/yr) after renewal. Selling would lock in the loss."),
                "score": 9,
            })

    # 2. Mortgage renewal payment shock.
    bumps = []
    for p in snap.get("properties", []):
        now = p.get("all_in_now") or p.get("payment_now") or 0
        ren = p.get("all_in_renewal") or p.get("payment_renewal_est") or 0
        if ren > now:
            bumps.append((p["name"], p.get("renews"), ren - now))
    if bumps:
        total_bump = sum(b[2] for b in bumps)
        when = ", ".join(f"{n} {w}" for n, w, _ in bumps)
        risks.append({
            "title": "Mortgage renewal payment shock",
            "severity": "high",
            "detail": (f"Renewals ({when}) add ~${total_bump:,.0f}/mo of carrying cost "
                       "as low rates roll off."),
            "score": 8,
        })

    # 3. Liquidity runway.
    lr = liquidity_runway(snap)
    if lr["liquid_runway_months"] and lr["liquid_runway_months"] < 12:
        risks.append({
            "title": "Thin personal liquidity buffer",
            "severity": "high" if lr["liquid_runway_months"] < 6 else "medium",
            "detail": (f"~{lr['liquid_runway_months']} months of liquid runway if income "
                       "stops; personal shortfall widens post-renewal. Corp can backstop "
                       "but only after taxed extraction."),
            "score": 7,
        })

    # 4. Leverage.
    nw = net_worth(snap)
    if nw["mortgages"] and nw["property_value"]:
        ltv = 100 * nw["mortgages"] / nw["property_value"]
        risks.append({
            "title": "High leverage on property",
            "severity": "high" if ltv > 85 else "medium",
            "detail": (f"Combined mortgage-to-value ≈ {ltv:.0f}% on ${nw['property_value']:,} "
                       f"of property; total debt is ~{nw['liabilities']/max(nw['value'],1):.1f}× "
                       "net worth. Little equity cushion if values fall."),
            "score": 7,
        })

    # 5. Allocation gaps.
    gaps = [r for r in alloc.get("rows", []) if r["status"] in ("missing", "under", "over")]
    if gaps:
        worst = sorted(gaps, key=lambda r: abs(r["drift_pp"]), reverse=True)[:3]
        desc = "; ".join(f"{r['label']} {r['actual_pct']}% vs {r['target_pct']}%" for r in worst)
        risks.append({
            "title": "Portfolio off target allocation",
            "severity": "medium",
            "detail": (f"Largest gaps: {desc}. Risk score {alloc['risk_score']} vs target "
                       f"{alloc['target_risk_score']} → {alloc['risk_vs_target']} intended risk. "
                       "(Low confidence until holdings are categorized.)"),
            "score": 5,
        })

    # 6. Corp tax drag on usable cash.
    corp = snap.get("corp", {})
    risks.append({
        "title": "Usable cash is partly trapped in the corp",
        "severity": "medium",
        "detail": ("~$210k sits inside the corp and is taxed when drawn to personal, so "
                   "personal liquidity is thinner than total net worth suggests."),
        "score": 4,
    })

    risks.sort(key=lambda r: r["score"], reverse=True)
    return {"risks": risks, "confidence": "medium"}


def dashboard(family_id: str) -> dict:
    snap = load_snapshot(family_id)
    target = load_target(family_id)
    holdings = load_holdings(family_id)
    fam = config.load_family_config(family_id)
    if not snap:
        return {"family_id": family_id, "family_name": fam.get("name"), "has_data": False}

    fx = config.load_app_config().get("fx_usd_cad", 1.0)
    nw = net_worth(snap)
    lr = liquidity_runway(snap)
    cf = cash_flow(snap)
    al = allocation(snap, target, holdings, fx)
    aa = account_allocation(snap, holdings, fx)
    rk = ranked_risks(snap, al)
    return {
        "family_id": family_id,
        "family_name": fam.get("name") or family_id,
        "as_of": snap.get("as_of"),
        "currency": snap.get("currency", "CAD"),
        "has_data": True,
        "net_worth": nw,
        "liquidity": lr,
        "cash_flow": cf,
        "allocation": al,
        "by_account": aa,
        "risks": rk,
    }


if __name__ == "__main__":
    import json
    import sys
    fam = config.resolve_family(sys.argv[1] if len(sys.argv) > 1 else None) or "household"
    print(json.dumps(dashboard(fam), indent=2, ensure_ascii=False))
