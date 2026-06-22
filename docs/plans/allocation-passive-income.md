# Plan — Show monthly passive income across the Allocation page

**Status: ✅ SHIPPED (2026-06-22).** Backend: `DEFAULT_INCOME_YIELD` + per-class `income_yield` +
`asset_base` in `allocation_calculator`. Frontend: `portfolioIncome` in `metrics`, $/mo shown in
Target card, "Your target mix" row, and saved mixes; editable income-yield field per class
(persists via `statsPayload`→stats). Verified render + edit round-trip. Only views.py +
web/allocation.html changed.

## Goal
Show **monthly passive income in $** for any mix, everywhere portfolio metrics appear (per the
circled screenshots): the **Target portfolio** card, the **Vs. popular allocations** rows, and each
**Saved mix** row — alongside return / σ / drawdown.

## Model (decided)
- **Base = allocation asset_base ($706,081)** — the book base the allocation %s are slices of, so
  %×base = real dollars held. (Gross $2.08M overstates: property pays no financial yield; liquid
  $488k mismatches the %s that include RE equity.)
- **Income = (Σ weightᵢ × income_yieldᵢ) × base ÷ 12.** Income yield is the *income-only* portion of
  return (dividends/coupons/interest/rent), separate from price appreciation.
- **Default income yields (%/yr, editable like ret/vol/maxdd):** cash 0.5 · tbill 4.0 · bonds 4.5 ·
  real_estate 0.0 (their RE nets ~0) · dividend 3.5 · gold 0 · growth 0.5 · trade 0 · crypto 0.
  (Sanity: current target → 1.85% → ~$1,089/mo; Option A+ → 2.33% → ~$1,368/mo.)

## Chunks (~15 min)
1. **Backend** (`views.py`): `DEFAULT_INCOME_YIELD` map; in `allocation_calculator` add
   `income_yield` to each class (saved-stat override else default) and `asset_base` to the payload.
2. **Frontend display** (`web/allocation.html`): read `assetBase` + per-class `income_yield`;
   extend `metrics()` to return `income = Σ(w·yield)/100 × base / 12`. Render "$X/mo passive income"
   in the Target card (new stat), each popular-allocation row, and each saved-mix row.
3. **Editable yields**: add an income-yield field to the per-class stat editor; include `income_yield`
   in `statsPayload()` so it persists (PUT /api/allocation → save_alloc_plan stats).
4. **Test**: restart, headless-render; verify target card + popular + saved mixes show $/mo; verify
   editing a yield updates totals and persists; current-target ≈ $1,089/mo.

## Notes
- No data migration: classes without a saved `income_yield` fall back to defaults.
- `save_alloc_plan` already round-trips `stats`; just widen the per-class stat shape by one field.
