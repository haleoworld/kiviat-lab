# Plan — Receipt filters, category tax auto-fill, transaction reconciliation

**Approved design:** 2026-06-21. Three independent groups; each ships and is testable on its own.
**Status: ✅ ALL SHIPPED (2026-06-21).** Built in order Group 3 → 1 → 2; each restart-tested, data intact.

---

## Group 1 — Receipts: "needs attention" + multi-filter  ✅ DONE

**Backend (`server.py` / `business.py`):**
- `GET /api/business/receipts` returns, per receipt: **`fiscal_year`** (computed via business config,
  like statements) and a **`flags`** array + **`needs_attention`** bool. Flag rules:
  - `duplicate` — confidence_reason contains "duplicate"
  - `refund` — reason/description mentions refund/return, or a negative total
  - `low_confidence` — confidence == low
  - `tax_mismatch` — `subtotal+gst+pst+hst+tip ≠ total` (beyond $0.02)
  - `has_note` — any confidence_reason present
- Also return distinct **vendors / categories / fiscal years** for the filter dropdowns.

**UI (`web/business.html`):** a filter bar above the review list, all combinable (client-side):
- **⚠ Needs attention** quick toggle (any flag set)
- **vendor** search · **category** select · **date range** (from/to) · **fiscal year** · **confidence** · **status**
- Show "showing N of M"; each flagged receipt shows its flag chips.

## Group 2 — Per-category tax rates + auto-fill HST  ✅ DONE
<!-- Shipped: business.load/save_category_tax + DEFAULT_CATEGORY_TAX (gift cards/bank fees off),
     apply_tax_estimate on ingest + reestimate_tax backfill (50 receipts), hst_estimated flag
     (None/True/False), manual tax edit → False, "HST est." badge, Settings category-tax table +
     re-estimate button, POST /api/business/receipts/reestimate-tax. -->


**Config (`business/config.yaml`):** `category_tax_rates: {<category>: {rate: 13, taxable: true}}`
(defaults: most expense categories 13% taxable; gift cards / fines not taxable).

**Settings → Corporation:** an editable table — each category → **rate %** + **taxable** toggle.

**Auto-fill (`business.py`):** when a receipt is `taxable`, has a category rate, and **no tax captured**
(gst+pst+hst all 0) on a tax-inclusive total, **back the HST out of the total**:
`hst = total × rate/(100+rate)`, `subtotal = total − hst − tip`. Mark **`hst_estimated: true`** so the
UI shows an "est." tag and the user can override (a manual edit clears the estimate).
- Applied on new parses, and via a **"Re-estimate tax"** backfill action for existing receipts.

## Group 3 — Statement transactions: reconciliation toggles  ✅ DONE

**Backend (`statements.py`):** transactions gain two fields (default false): **`no_receipt`**, **`reconciled`**.
`update_transaction` accepts both. `_EDITABLE` extended.

**UI (`web/statements.html`):** per transaction, two small toggles — **No receipt** and **✓ Reconciled**.
A **state filter** on the page: all / unreconciled / reconciled / no-receipt — so you can work down the
open ones. (Phase 3 matching will later flip `reconciled` automatically when a receipt links.)

---

## Build order & notes
1. Group 3 (smallest: two flags + toggles + filter). 2. Group 1 (filters). 3. Group 2 (tax config + auto-fill).
Each restart-tested; back up `receipts.jsonl` / `statements.jsonl` before any batch write (auto-fill backfill).
Auto-fill never overwrites a user-entered tax; estimates are flagged and reversible.
