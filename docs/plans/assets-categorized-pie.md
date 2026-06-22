# Plan — Align Assets pie with Allocation categories (keep accounts)

**Status: ✅ SHIPPED (2026-06-21).** Backend `holdings_equity_mix` + `/api/assets` field;
`allocByCategory` + `CAT_META` drive the Asset-allocation card/chart only. Verified: pie shows
all categories matching Allocation; net worth ($530,007), Liquidity, and the account-based item
list unchanged. No data writes.

## Problem
Assets page lists **one row per account** (TFSA/RRSP/Corp…), all tagged class `equities`, so its
"Asset allocation" pie shows a single Equities lump. Allocation page splits by **holding category**
(growth/dividend/trade/crypto/tbill) pooled across accounts. An account is multi-category, so the
account-based item list can't carry one category.

## Decision (locked)
Split **only the Asset-allocation pie/chart** by category, derived from holdings; keep the editable
item list AND Liquidity card account-based and untouched. Net worth never changes (pie equity total
stays tied to the user's tracked items; only its internal split changes).

Brokerage holdings mix (book, FX): growth 2.7% · dividend 16.7% · trade 25.5% · crypto 1.4% ·
T-bill/MMF 53.8%. (The lump was 54% T-bill — the visible discrepancy.)

## Chunks (~15 min)
1. **Backend** `views.holdings_equity_mix(fid)` → `{equities_growth, equities_dividend,
   equities_trade, crypto, cash_tbill}` as fractions (book, USD→CAD). Empty dict if no holdings.
   Add `holdings_mix` to `/api/assets` payload.
2. **Frontend** `web/assets.html`:
   - CLASSES: add `equities_growth/dividend/trade` + `cash_tbill` (labels + Allocation colors).
   - `allocByCategory(m)`: take `allocByClass(m)`, explode its `equities` bucket by `holdings_mix`
     (slice = equityTotal × frac); pass everything else through. Fallback to single `equities`
     slice if mix empty.
   - Point **allocCard + allocChart only** at `allocByCategory`; update subtitle ("equities split
     by category, from holdings"). Item list / Liquidity / Net chart / Changes: unchanged.
3. **Test**: restart (`launchctl kickstart -k`), headless-render Assets vs Allocation; confirm
   equity proportions match, item list + liquidity + net-worth total unchanged. No data writes.
