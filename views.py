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

import datetime
from typing import Any

import yaml

import config
import paths


def age_from_birthday(bday: str | None) -> int | None:
    """Whole years from an ISO birthday (YYYY-MM-DD) to today, or None if unparseable."""
    if not bday:
        return None
    try:
        b = datetime.date.fromisoformat(str(bday)[:10])
    except ValueError:
        return None
    t = datetime.date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def primary_member(family_id: str) -> dict | None:
    """The household's main person: the one flagged primary, else the first member."""
    members = config.load_members(family_id)
    if not members:
        return None
    return next((m for m in members if m.get("primary")), members[0])

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


# Categories that live INSIDE brokerage accounts — used to split the Assets page's
# single "Equities" bucket the same way the Allocation page splits it.
_EQUITY_MIX_CATS = ("equities_growth", "equities_dividend", "equities_trade",
                    "crypto", "cash_tbill")


def holdings_equity_mix(family_id: str) -> dict:
    """Fractional split of brokerage holdings across the equity-side categories (book, USD→CAD).
    Returns {category: fraction} summing to 1, or {} when there are no holdings — the Assets page
    applies these proportions to its tracked equity total so its pie matches the Allocation page
    without changing net worth."""
    holdings = load_holdings(family_id)
    if not holdings:
        return {}
    fx = config.load_app_config().get("fx_usd_cad", 1.0) or 1.0
    by_cat: dict[str, float] = {}
    for h in holdings:
        cat = h.get("category", "equities_uncat")
        if cat not in _EQUITY_MIX_CATS:
            continue
        book = (h.get("book") or 0) * (fx if h.get("currency") == "USD" else 1)
        by_cat[cat] = by_cat.get(cat, 0) + book
    total = sum(by_cat.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in by_cat.items()}


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


# ---------- retirement calculator defaults ----------

# Map each account's `tax` tag to a coarse, grossed-up "withdrawal tax" bucket.
# Rates are defaults the user edits in the UI; they drive the blended gross-up that
# turns an after-tax spending need into a pre-tax nest-egg requirement.
RETIRE_BUCKETS = {
    "tax_free":     {"key": "tax_free",     "label": "Tax-free (TFSA + cash)", "rate": 0},
    "none":         {"key": "tax_free",     "label": "Tax-free (TFSA + cash)", "rate": 0},
    "deferred":     {"key": "deferred",     "label": "RRSP / registered",      "rate": 30},
    "corp_extract": {"key": "corp",         "label": "Corporate accounts",     "rate": 35},
}


def retirement_defaults(family_id: str) -> dict:
    """Auto-populate the retirement calculator from the family's snapshot.

    Everything here is a *default* the user can overwrite in the UI. The tax
    buckets exist only to compute a blended withdrawal-tax rate, which grosses up
    an after-tax spending need into the pre-tax nest egg the headline reports.
    """
    snap = load_snapshot(family_id)
    if not snap:
        return {"has_data": False}

    nw = net_worth(snap)
    cf = cash_flow(snap)
    exp = snap.get("expenses_monthly", {}) or {}

    # Build editable tax buckets by grouping accounts on their `tax` tag.
    # RESPs are the kids' education money, not retirement — leave them out.
    grouped: dict[str, dict] = {}
    for a in snap.get("accounts", []):
        if (a.get("name") or "").upper().startswith("RESP"):
            continue
        spec = RETIRE_BUCKETS.get(a.get("tax", "none"), RETIRE_BUCKETS["none"])
        b = grouped.setdefault(spec["key"], {"key": spec["key"], "label": spec["label"],
                                             "balance": 0.0, "rate": spec["rate"]})
        b["balance"] += a.get("value") or 0

    # Real-estate equity (value − mortgage). Primary residence is CG-exempt in
    # Canada, a rental isn't, so default a low blended rate the user can raise.
    re_equity = sum(((p.get("value") or 0) - (p.get("mortgage") or 0))
                    for p in snap.get("properties", []))
    if re_equity:
        grouped["real_estate"] = {"key": "real_estate", "label": "Real-estate equity",
                                  "balance": re_equity, "rate": 0}

    order = ["tax_free", "deferred", "corp", "real_estate"]
    buckets = [{**grouped[k], "balance": round(grouped[k]["balance"])}
               for k in order if k in grouped]

    # Monthly savings default: the household's net annual surplus (incl. corp
    # accumulation), floored at 0 — what's actually being added to the pile.
    monthly_savings = max(round((cf.get("household_net_year_now") or 0) / 12), 0)

    # Current age from the primary member's birthday (set in Settings), else 40.
    pm = primary_member(family_id)
    current_age = age_from_birthday(pm.get("birthday")) if pm else None

    return {
        "has_data": True,
        "currency": snap.get("currency", "CAD"),
        "as_of": snap.get("as_of"),
        # Headline auto-fills (all editable in the UI):
        "net_assets": nw["value"],                      # computed net worth
        "monthly_savings": monthly_savings,             # household surplus / mo
        "monthly_expenses": round(exp.get("total_now") or 0),  # after-tax spend / mo
        # Sensible starting assumptions (matching the reference calculator):
        "current_age": current_age if current_age is not None else 40,
        "retire_age": 65,
        "death_age": 84,
        "return_pct": 7,
        "inflation_pct": 3,
        # Tax buckets drive the blended gross-up (after-tax → pre-tax):
        "tax_buckets": buckets,
        "notes": ("Net assets = computed net worth. Monthly savings = household net "
                  "surplus incl. corp accumulation. Expenses default to your current "
                  "after-tax monthly spend. RESPs excluded (kids). All fields editable."),
    }


# Inputs the user is allowed to persist (everything else is ignored on save).
RETIRE_INPUT_KEYS = {
    "current_age", "retire_age", "death_age", "net_assets", "monthly_savings",
    "monthly_expenses", "return_pct", "inflation_pct", "tax_buckets",
    "scenarios",   # named saved retirement scenarios
}


def _retirement_file(family_id: str):
    return paths.family_dir(family_id) / "retirement.yaml"


def load_retirement_inputs(family_id: str) -> dict | None:
    """User-saved retirement-calculator overrides, or None if never saved."""
    f = _retirement_file(family_id)
    if not f.exists():
        return None
    return yaml.safe_load(f.read_text()) or None


def save_retirement_inputs(family_id: str, data: dict) -> dict:
    """Persist only the whitelisted input keys to families/<id>/retirement.yaml."""
    clean = {k: data[k] for k in RETIRE_INPUT_KEYS if k in data}
    f = _retirement_file(family_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump(clean, sort_keys=False, allow_unicode=True))
    return clean


def clear_retirement_inputs(family_id: str) -> None:
    """Drop saved overrides so the calculator falls back to computed defaults."""
    f = _retirement_file(family_id)
    if f.exists():
        f.unlink()


# ---------- finances (categorized monthly history) ----------

def _finances_file(family_id: str):
    return paths.family_dir(family_id) / "finances.yaml"


def load_finances(family_id: str) -> dict:
    f = _finances_file(family_id)
    if not f.exists():
        return {"months": {}}
    data = yaml.safe_load(f.read_text()) or {}
    return {"months": data.get("months", {})}


def save_finances(family_id: str, data: dict) -> dict:
    months = (data or {}).get("months") if isinstance(data, dict) else None
    if not isinstance(months, dict):
        months = {}
    f = _finances_file(family_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump({"months": months}, sort_keys=True, allow_unicode=True))
    return {"months": months}


def finances_seed(family_id: str) -> dict:
    """Build a starter month (income / expenses / debt / savings) from the snapshot,
    so the Finances page pre-fills instead of starting blank."""
    snap = load_snapshot(family_id)
    if not snap:
        return {"income": [], "expenses": [], "debt": [], "savings": []}

    expenses = [{"category": e.get("category"), "amount": e.get("amount") or 0}
                for e in (snap.get("expenses_detail") or [])]

    income = []
    det = (snap.get("income_monthly", {}) or {}).get("detail", {}) or {}
    if det.get("you_draws"):
        income.append({"category": "Your draws", "amount": det["you_draws"]})
    if det.get("wife_net"):
        income.append({"category": "Spouse net", "amount": det["wife_net"]})
    for p in snap.get("properties", []):
        if p.get("rent_mo"):
            income.append({"category": f"Rent — {p['name']}", "amount": p["rent_mo"]})

    debt = []
    for p in snap.get("properties", []):
        if p.get("mortgage"):
            debt.append({"category": f"Mortgage — {p['name']}",
                         "balance": p.get("mortgage") or 0,
                         "payment": p.get("payment_now") or 0})
    for d in snap.get("debts", []):
        debt.append({"category": d.get("name"),
                     "balance": d.get("balance") or 0,
                     "payment": d.get("monthly") or 0})

    savings = []
    corp = snap.get("corp", {}) or {}
    if corp.get("annual_accumulation"):
        savings.append({"category": "Corp accumulation",
                        "amount": round(corp["annual_accumulation"] / 12)})

    return {"income": income, "expenses": expenses, "debt": debt, "savings": savings}


def finances_seed_month(family_id: str) -> str:
    """The month key (YYYY-MM) the seed represents — the snapshot's as-of month."""
    snap = load_snapshot(family_id)
    return (snap.get("as_of") or "2026-06")[:7]


# ---------- assets / net worth (monthly history, preset-driven) ----------

def _assets_file(family_id: str):
    return paths.family_dir(family_id) / "assets.yaml"


def load_assets(family_id: str) -> dict:
    f = _assets_file(family_id)
    if not f.exists():
        return {"months": {}}
    data = yaml.safe_load(f.read_text()) or {}
    return {"months": data.get("months", {})}


def save_assets(family_id: str, data: dict) -> dict:
    months = (data or {}).get("months") if isinstance(data, dict) else None
    if not isinstance(months, dict):
        months = {}
    f = _assets_file(family_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump({"months": months}, sort_keys=True, allow_unicode=True))
    return {"months": months}


# Coarse asset class for each snapshot account (account-level allocation).
_ACCT_CLASS = {"cash_tbill": "cash"}  # everything else defaults to equities


def assets_seed(family_id: str) -> dict:
    """Starter asset list from the snapshot: real estate (with monthly cash flow so
    the Rich-Dad cash-flow test can classify it) + investment/cash accounts."""
    snap = load_snapshot(family_id)
    if not snap:
        return {"items": []}
    items = []
    for p in snap.get("properties", []):
        # cash flow: rental net bleed if present (negative), else all-in carry (negative).
        bleed = p.get("net_bleed_mo_now")
        cf = -(bleed) if bleed else -(p.get("all_in_now") or 0)
        items.append({"name": p.get("name"), "class": "real_estate",
                      "value": p.get("value") or 0, "cashflow": round(cf)})
    for a in snap.get("accounts", []):
        cls = _ACCT_CLASS.get(a.get("asset_class"), "equities")
        items.append({"name": a.get("name"), "class": cls,
                      "value": a.get("value") or 0, "cashflow": 0})
    return {"items": items}


# ---------- allocation / risk-return calculator ----------

# Researched historical figures per asset class (annual % return, annualized
# volatility, worst historical max drawdown). Defaults the user can fine-tune.
# Sources gathered 2026-06: S&P 500 ~10.3%/σ16%/-55%; Nasdaq-100 ~13.8%/σ17%/dot-com
# -83% (we default a representative -60%); REIT ~11%/σ17.5%/GFC -68%; high-dividend
# ~9-10%/σ13%/-50%; gold ~8%/σ~20%/-45%; T-bills ~3.3%/~0 drawdown; 3x-leveraged
# (TQQQ) maxDD ~-82% (return regime-dependent, haircut to 18%); Bitcoin 10y ~66%
# (haircut to 35% forward) / σ~70% / -82%.
RESEARCHED_STATS = {
    # Cash (chequing/savings) earns ~0; T-bills / money-market earn a real yield.
    "cash":              {"ret": 0.5, "vol": 0,  "maxdd": 0,   "risk": 1},
    "tbill":             {"ret": 4,   "vol": 1,  "maxdd": 0,   "risk": 1},
    # Bonds = aggregate investment-grade index: ~4-5% return, σ~6%, worst-ever
    # drawdown ~-17% (the 2022 rate shock; historically much shallower).
    "bonds":             {"ret": 4.5, "vol": 6,  "maxdd": -17, "risk": 2},
    # Real estate = Toronto/GTA direct housing (their market), NOT US REITs:
    # ~7% CAGR 1996-2021 (≈6% incl. the 2022-26 correction); worst drawdown ~-28%
    # (1989-96) / -24%+ (2022-26, condos/suburbs deeper); σ ~10% but smoothed.
    "real_estate":       {"ret": 6,   "vol": 10, "maxdd": -30, "risk": 2},
    "equities_dividend": {"ret": 9.5, "vol": 13, "maxdd": -50, "risk": 3},
    "gold":              {"ret": 8,   "vol": 18, "maxdd": -45, "risk": 3},
    "equities_uncat":    {"ret": 10,  "vol": 16, "maxdd": -55, "risk": 4},
    "equities_growth":   {"ret": 13,  "vol": 18, "maxdd": -60, "risk": 4},
    "equities_trade":    {"ret": 18,  "vol": 45, "maxdd": -80, "risk": 5},
    "crypto":            {"ret": 35,  "vol": 70, "maxdd": -82, "risk": 5},
}
ALLOC_ORDER = ["cash", "tbill", "bonds", "real_estate", "equities_dividend", "gold",
               "equities_growth", "equities_trade", "crypto"]
ALLOC_LABELS = {"bonds": "Bonds / Fixed income", "cash": "Cash",
                "tbill": "T-Bills / money market"}
# Borrowing cost subtracted from leveraged return (mortgage/margin rate, %).
DEFAULT_BORROW_RATE = 4.0
# Income-only yield per class (%/yr) — the dividends/coupons/interest/rent portion of return,
# used for monthly passive-income estimates. Editable per class (saved in plan stats). Gold,
# growth, trade and crypto pay ~nothing; real estate nets ~0 for this household (condo bleeds).
DEFAULT_INCOME_YIELD = {
    "cash": 0.5, "tbill": 4.0, "bonds": 4.5, "real_estate": 0.0, "equities_dividend": 3.5,
    "gold": 0.0, "equities_growth": 0.5, "equities_trade": 0.0, "crypto": 0.0,
}


def _re_leverage(snap: dict) -> float:
    """Real-estate leverage on EQUITY = property book ÷ equity (book − mortgage).
    ~8× at 87% LTV for this household. The allocation weights RE at equity, so
    leverage amplifies the % return/risk on that thin stake."""
    book = sum((p.get("purchase_price") or p.get("value") or 0) for p in snap.get("properties", []))
    equity = sum((p.get("purchase_price") or p.get("value") or 0) - (p.get("mortgage") or 0)
                 for p in snap.get("properties", []))
    return round(book / equity, 1) if equity > 0 and book > 0 else 1.0


def _alloc_plan_file(family_id: str):
    return paths.family_dir(family_id) / "allocation_plan.yaml"


def load_alloc_plan(family_id: str) -> dict:
    f = _alloc_plan_file(family_id)
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text()) or {}


def save_alloc_plan(family_id: str, data: dict) -> dict:
    plan = {
        "targets": data.get("targets") if isinstance(data, dict) else None,
        "stats": data.get("stats") if isinstance(data, dict) else None,
        "borrow": data.get("borrow") if isinstance(data, dict) else None,
        "mixes": data.get("mixes") if isinstance(data, dict) else None,
    }
    f = _alloc_plan_file(family_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True))
    return plan


def allocation_calculator(family_id: str) -> dict:
    """Per-class researched stats merged with any saved edits, the family's CURRENT
    allocation %, and any saved target mix — everything the calculator page needs."""
    snap = load_snapshot(family_id)
    current = {}
    if snap:
        fx = config.load_app_config().get("fx_usd_cad", 1.0)
        al = allocation(snap, load_target(family_id), load_holdings(family_id), fx)
        current = {r["category"]: r["actual_pct"] for r in al.get("rows", [])}
        # The dashboard lumps cash + T-bills as one bucket; split it here.
        # Chequing accounts (asset_class cash_tbill) = actual Cash; the rest of
        # the bucket (T-bill / money-market ETFs from holdings) = T-Bills.
        base = al.get("asset_base", 1) or 1
        chequing = sum((a.get("value") or 0) for a in snap.get("accounts", [])
                       if a.get("asset_class") == "cash_tbill")
        ct = current.pop("cash_tbill", 0)
        current["cash"] = round(100 * chequing / base, 1)
        current["tbill"] = round(ct - current["cash"], 1)

    plan = load_alloc_plan(family_id)
    saved_stats = plan.get("stats") or {}
    re_lev = _re_leverage(snap) if snap else 1.0
    default_lev = {"real_estate": re_lev}   # all others unlevered (trade already baked in)
    asset_base = 0      # full allocation base (incl. real-estate book equity)
    re_equity = 0       # real-estate book equity within that base
    if snap:
        al2 = allocation(snap, load_target(family_id), load_holdings(family_id),
                         config.load_app_config().get("fx_usd_cad", 1.0))
        asset_base = al2.get("asset_base", 0)
        re_equity = next((r["actual_value"] for r in al2.get("rows", [])
                          if r["category"] == "real_estate"), 0)
    invest_base = max(0, asset_base - re_equity)   # investable/financial only (excl. illiquid RE)
    classes = []
    for k in ALLOC_ORDER:
        d = RESEARCHED_STATS[k]
        s = saved_stats.get(k, {})
        classes.append({
            "key": k, "label": ALLOC_LABELS.get(k, CATEGORY_LABELS.get(k, k)),
            "ret": s.get("ret", d["ret"]), "vol": s.get("vol", d["vol"]),
            "maxdd": s.get("maxdd", d["maxdd"]), "risk": d["risk"],
            "lev": s.get("lev", default_lev.get(k, 1)),
            "income_yield": s.get("income_yield", DEFAULT_INCOME_YIELD.get(k, 0)),
            "current_pct": round(current.get(k, 0), 1),
        })
    # Retirement horizon for the growth projection (prefer saved inputs, else defaults).
    ri = load_retirement_inputs(family_id)
    if not (ri and ri.get("current_age") and ri.get("retire_age")):
        ri = retirement_defaults(family_id)
    retire = {"current_age": ri.get("current_age"), "retire_age": ri.get("retire_age"),
              "inflation_pct": ri.get("inflation_pct", 3)}
    return {
        "classes": classes,
        "saved_targets": plan.get("targets"),
        "mixes": plan.get("mixes") or [],
        "borrow": plan.get("borrow") or DEFAULT_BORROW_RATE,
        "asset_base": round(asset_base),
        "invest_base": round(invest_base),
        "retire": retire,
        "currency": snap.get("currency", "CAD") if snap else "CAD",
    }


# ---------- DCA planner ----------

# How many contributions per year each cadence implies.
DCA_FREQS = {"twice_weekly": 104, "weekly": 52, "biweekly": 26, "monthly": 12}
DCA_FREQ_LABELS = {"twice_weekly": "Twice a week", "weekly": "Weekly",
                   "biweekly": "Every 2 weeks", "monthly": "Monthly"}
DEFAULT_DCA_FREQ = "weekly"
DEFAULT_DCA_MONTHS = 12


def _dca_settings_file(family_id: str):
    return paths.family_dir(family_id) / "dca.yaml"


def load_dca_settings(family_id: str) -> dict:
    f = _dca_settings_file(family_id)
    data = (yaml.safe_load(f.read_text()) or {}) if f.exists() else {}
    freq = data.get("frequency")
    if freq not in DCA_FREQS:
        freq = DEFAULT_DCA_FREQ
    timeframes = data.get("timeframes") or {}
    return {"frequency": freq, "timeframes": timeframes}


def save_dca_settings(family_id: str, data: dict) -> dict:
    cur = load_dca_settings(family_id)
    freq = (data or {}).get("frequency", cur["frequency"])
    if freq not in DCA_FREQS:
        freq = cur["frequency"]
    timeframes = dict(cur["timeframes"])
    for k, v in ((data or {}).get("timeframes") or {}).items():
        try:
            m = int(v)
        except (TypeError, ValueError):
            continue
        if m > 0:
            timeframes[k] = m
    out = {"frequency": freq, "timeframes": timeframes}
    f = _dca_settings_file(family_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True))
    return out


def dca_plan(family_id: str) -> dict:
    """Per-class DCA contribution suggestions on the LIQUID base (real-estate equity excluded,
    since it can't be DCA'd). Targets are rescaled across the movable classes to sum 100%.

    For each underweight class, the single-class self-dilution contribution to reach target is
    `C = T_liq × (target_frac − current_frac) / (1 − target_frac)`; overweight classes (C ≤ 0) are
    flagged "trim" with no contribution. Per-period = C / (periods_per_year × months / 12), shown
    in CAD and USD-equivalent (`USD = CAD / fx_usd_cad`).
    """
    snap = load_snapshot(family_id)
    settings = load_dca_settings(family_id)
    fx = config.load_app_config().get("fx_usd_cad", 1.0) or 1.0
    per_year = DCA_FREQS[settings["frequency"]]
    plan = load_alloc_plan(family_id)
    tg = plan.get("targets") or {}

    ac = allocation_calculator(family_id)
    cur_pct = {c["key"]: c["current_pct"] for c in ac["classes"]}

    full_base = 0
    re_value = 0
    if snap:
        al = allocation(snap, load_target(family_id), load_holdings(family_id), fx)
        full_base = al.get("asset_base", 0) or 0
        re_value = next((r["actual_value"] for r in al.get("rows", [])
                         if r["category"] == "real_estate"), 0)
    t_liq = full_base - re_value

    movable = [k for k in ALLOC_ORDER if k != "real_estate"]
    tgt_sum = sum(tg.get(k, 0) for k in movable) or 1   # ~95 (100 − real_estate target)

    rows = []
    for k in movable:
        # Current/target as fractions of the LIQUID base.
        cur_val = (cur_pct.get(k, 0) / 100.0) * full_base
        cur_frac = (cur_val / t_liq) if t_liq else 0
        tgt_frac = tg.get(k, 0) / tgt_sum
        months = int(settings["timeframes"].get(k, DEFAULT_DCA_MONTHS) or DEFAULT_DCA_MONTHS)
        periods = max(1, round(per_year * months / 12))
        if tgt_frac >= 1:
            c = 0.0
        else:
            c = t_liq * (tgt_frac - cur_frac) / (1 - tgt_frac)
        gap = round(tgt_frac * 100 - cur_frac * 100, 1)
        if c > 1:
            direction = "add"
            per_cad = c / periods
        elif c < -1:
            direction = "trim"
            per_cad = 0.0
        else:
            direction = "ontarget"
            per_cad = 0.0
        rows.append({
            "key": k,
            "label": ALLOC_LABELS.get(k, CATEGORY_LABELS.get(k, k)),
            "current_pct": round(cur_frac * 100, 1),
            "target_pct": round(tgt_frac * 100, 1),
            "gap_pp": gap,
            "gap_cad": round(c),
            "months": months,
            "periods": periods,
            "direction": direction,
            "per_period_cad": round(per_cad),
            "per_period_usd": round(per_cad / fx) if fx else None,
        })

    return {
        "rows": rows,
        "base": round(t_liq),
        "full_base": round(full_base),
        "real_estate_value": round(re_value),
        "real_estate_pct": round(100 * re_value / full_base, 1) if full_base else 0,
        "frequency": settings["frequency"],
        "freq_per_year": per_year,
        "freq_labels": DCA_FREQ_LABELS,
        "fx_usd_cad": fx,
        "confidence": ac.get("confidence", "medium") if isinstance(ac, dict) else "medium",
        "has_targets": bool(tg),
    }


# ---------- monthly reminder state ----------

def _reminders_file():
    return paths.DATA_ROOT / "reminders.yaml"


def _load_reminders() -> dict:
    f = _reminders_file()
    return (yaml.safe_load(f.read_text()) or {}) if f.exists() else {}


def _done_map(data: dict) -> dict:
    """Per-task done cycles, migrating the legacy single-task {done_for: X} shape."""
    if isinstance(data.get("done"), dict):
        return dict(data["done"])
    if "done_for" in data:
        return {"finance": data["done_for"]}
    return {}


def reminder_done_for(task: str = "finance"):
    """The YYYY-MM cycle this task was last marked done (so its reminders pause)."""
    return _done_map(_load_reminders()).get(task)


def mark_reminder_done(task: str, cycle: str) -> None:
    done = _done_map(_load_reminders())
    done[task] = cycle
    _reminders_file().write_text(yaml.safe_dump({"done": done}, sort_keys=False))


def assets_contributions(family_id: str) -> dict:
    """Net household cash flow added to the balance sheet per month, from the
    Finances page: income − expenses − debt payments + savings. This is the
    'money you added' that explains part of each month's net-worth change."""
    by_month = {}
    for m, mo in (load_finances(family_id).get("months") or {}).items():
        inc = sum((x.get("amount") or 0) for x in (mo.get("income") or []))
        exp = sum((x.get("amount") or 0) for x in (mo.get("expenses") or []))
        debtpay = sum((d.get("payment") or 0) for d in (mo.get("debt") or []))
        sav = sum((s.get("amount") or 0) for s in (mo.get("savings") or []))
        by_month[m] = round(inc - exp - debtpay + sav)
    return by_month


def assets_liabilities(family_id: str) -> dict:
    """Liabilities for the net calc = the Finances page's debt balances (single
    source of truth). Returns both per-month totals and the per-debt detail, so the
    Assets page's NET mode can net each loan against its matching asset.
    Falls back to the snapshot's debts (mortgages + other) when Finances is empty."""
    by_month, debts_by_month = {}, {}
    for m, mo in (load_finances(family_id).get("months") or {}).items():
        ds = [{"category": d.get("category"), "balance": d.get("balance") or 0}
              for d in (mo.get("debt") or [])]
        debts_by_month[m] = ds
        by_month[m] = sum(d["balance"] for d in ds)

    snap = load_snapshot(family_id)
    fallback_debts = []
    if snap:
        for p in snap.get("properties", []):
            if p.get("mortgage"):
                fallback_debts.append({"category": f"Mortgage — {p['name']}",
                                       "balance": p["mortgage"]})
        for d in snap.get("debts", []):
            fallback_debts.append({"category": d.get("name"), "balance": d.get("balance") or 0})
    fallback = sum(d["balance"] for d in fallback_debts)
    return {"by_month": by_month, "fallback": fallback,
            "debts_by_month": debts_by_month, "fallback_debts": fallback_debts}


# ---------- AI auto-fill ----------

class AIFillError(Exception):
    """Raised when the AI auto-fill can't complete; carries an HTTP status."""
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# Tool schema that forces Claude to return exactly the calculator's input shape.
_AIFILL_TOOL = {
    "name": "fill_retirement_inputs",
    "description": "Return retirement-calculator inputs for this household, in today's CAD dollars.",
    "input_schema": {
        "type": "object",
        "properties": {
            "current_age": {"type": "number", "description": "Best estimate of the primary earner's current age."},
            "retire_age": {"type": "number"},
            "death_age": {"type": "number", "description": "Planning life expectancy."},
            "net_assets": {"type": "number", "description": "Net worth available for retirement, CAD."},
            "monthly_savings": {"type": "number", "description": "Realistic monthly amount added to savings/investments now."},
            "monthly_expenses": {"type": "number", "description": "After-tax monthly spend expected IN RETIREMENT (drop costs that end, e.g. a paid-off mortgage; keep lifestyle)."},
            "return_pct": {"type": "number", "description": "Nominal annual investment return, given their actual allocation."},
            "inflation_pct": {"type": "number"},
            "tax_buckets": {
                "type": "array",
                "description": "Keep the provided keys/labels/balances; set each `rate` to the realistic blended withdrawal tax % for that bucket given this household's Ontario marginal rate.",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"}, "label": {"type": "string"},
                        "balance": {"type": "number"}, "rate": {"type": "number"},
                    },
                    "required": ["key", "label", "balance", "rate"],
                },
            },
            "rationale": {"type": "string", "description": "2-4 sentences explaining the key choices (esp. retirement expenses, return, and tax rates)."},
        },
        "required": ["current_age", "retire_age", "death_age", "net_assets", "monthly_savings",
                     "monthly_expenses", "return_pct", "inflation_pct", "tax_buckets", "rationale"],
    },
}


def ai_retirement_fill(family_id: str) -> dict:
    """Ask Claude to reason over the family's full financial picture and fill the
    retirement-calculator inputs. Returns the input dict plus a `rationale`.

    Distinct from `retirement_defaults` (simple heuristics): the model adjusts
    retirement expenses for costs that end (paid-off mortgages), picks a return
    consistent with the real allocation, and sets per-bucket withdrawal-tax rates
    from the household's actual tax position.
    """
    api_key = config.load_api_key()
    if not api_key:
        raise AIFillError("No ANTHROPIC_API_KEY set in the data-root .env.", status=400)
    try:
        from anthropic import Anthropic
    except ImportError:
        raise AIFillError("anthropic SDK not installed on the server.", status=500)

    snap = load_snapshot(family_id)
    if not snap:
        raise AIFillError("No snapshot to read for this family.", status=400)

    defaults = retirement_defaults(family_id)
    fam = config.load_family_config(family_id)
    members = [{**m, "age": age_from_birthday(m.get("birthday"))}
               for m in config.load_members(family_id)]
    context = {
        "snapshot": snap,
        "holdings": load_holdings(family_id),
        "household": fam.get("household", {}),
        "members": members,
        "currency": snap.get("currency", "CAD"),
        "heuristic_defaults": {k: defaults.get(k) for k in (
            "current_age", "retire_age", "death_age", "net_assets",
            "monthly_savings", "monthly_expenses", "return_pct", "inflation_pct")},
        "tax_buckets_seed": defaults.get("tax_buckets", []),
    }
    prompt = (
        "You are a Canadian (Ontario) retirement-planning assistant. Using ONLY the household "
        "financial data below, fill the retirement calculator via the fill_retirement_inputs tool. "
        "All figures in today's CAD dollars (real terms).\n\n"
        "Guidance:\n"
        "- monthly_expenses is the AFTER-TAX retirement spend. Start from current spending but drop "
        "costs that won't exist in retirement (e.g. a mortgage paid off by then, RRSP-loan payments, "
        "daycare once kids are grown); keep the underlying lifestyle.\n"
        "- return_pct should reflect their ACTUAL asset mix (more conservative if heavily real-estate "
        "or cash; higher if mostly equities).\n"
        "- For tax_buckets, keep each key/label/balance as seeded and set `rate` to the realistic "
        "withdrawal tax: ~0% for TFSA/cash, the marginal rate for RRSP, the integrated corp+personal "
        "rate for corporate funds, and the effective capital-gains rate on any taxable real estate "
        "(primary residence is exempt).\n"
        "- If a person's age isn't stated, infer a reasonable estimate from context and say so in the rationale.\n\n"
        "HOUSEHOLD DATA (YAML/JSON):\n" + yaml.safe_dump(context, sort_keys=False, allow_unicode=True)
    )

    model = config.load_app_config()["extraction"]["model"]
    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model, max_tokens=2000,
            tools=[_AIFILL_TOOL],
            tool_choice={"type": "tool", "name": "fill_retirement_inputs"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # network / auth / rate-limit
        raise AIFillError(f"Claude request failed: {e}", status=502)

    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        raise AIFillError("Claude did not return structured inputs.", status=502)
    out = dict(block.input)
    out["model"] = model
    return out


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
