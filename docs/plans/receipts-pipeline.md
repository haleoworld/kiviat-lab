# Plan — Receipts → Corp-Tax Pipeline

**Approved:** 2026-06-18. Full design lives in [`RECEIPTS_PIPELINE.md`](../../RECEIPTS_PIPELINE.md)
(repo root). This file is the execution plan: ~15-minute chunks, what ships when.

The pipeline has 5 phases; **each ships independently and is reviewable**. We build
Phase 1 now (it depends on none of the open tax/export decisions) so the receipts
Telegram reminder points at a working upload by **July 4**. Phases 2–5 wait on the
open decisions in §9 of the design doc.

---

## Phase 1 — Upload + AI parse + review  ← building now

**Goal:** snap or drag a business receipt/invoice → Claude parses it into a clean,
editable, structured record you approve. Nothing AI-extracted is trusted until you
approve it (constitution rule #5).

**Data area:** `data/families/household/business/`
- `uploads/<id>.<ext>` — the original file, kept as the audit trail (HEIC → JPEG on
  ingest so it renders on the phone).
- `receipts.jsonl` — one record per line; every receipt with a `review_status`
  (`pending` / `approved` / `rejected`).

**Record shape:** the design doc §2 Receipt record. Phase 1 fills everything through
`review_status`; `matched_txn_id` stays `null` until Phase 3.

### Chunks
1. **`business.py` backend** — paths/ensure-dirs, `RECEIPT_TOOL` schema, `parse_receipt`
   (Claude tool-use, reuses `extract.build_content_block`), `ingest_upload` (save file +
   HEIC convert + parse + append), `load_receipts` / `update_receipt` / `delete_receipt`,
   `upload_path`. Parse failures still create a `low`-confidence stub to fill by hand —
   never crash the worker (no `extract.die` / `sys.exit` in the request path).
2. **Server routes** — `GET /business` (page) and `/api/business/*`: `GET receipts`,
   `POST upload` (multipart), `PUT receipts/{id}`, `DELETE receipts/{id}`,
   `GET file/{id}` (serve original for the thumbnail). All behind the passcode.
3. **`web/business.html`** — upload card (camera/drag, multi-file) + review list:
   thumbnail, editable fields, confidence badge, tax-reconcile warning
   (`subtotal+gst+pst+hst+tip ≈ total`), Approve / Reject / Delete, debounced autosave —
   matching the settings/finances page conventions.
4. **Nav + test** — add a **Business → Receipts** link to `_nav.html`; kickstart prod;
   verify upload→parse→review→approve end-to-end via headless Chrome on a sample receipt.
   Back up any YAML/JSONL before write-path tests; confirm user data intact after.
5. **Accountant's categories** (shipped) — receipts categorize into the *exact* FY2025 buckets
   (see memory `corp-chart-of-accounts`): the `RECEIPT_TOOL.category` is an `enum`, and the edit
   field is a constrained dropdown served from `business.ALL_CATEGORIES`. AI mapping hints encode
   the non-obvious rules (client gifts → Advertising and Promotion; fuel/parking → Vehicle expense).
6. **Exact-duplicate guard** (shipped) — `ingest_upload` hashes the original upload bytes
   (sha256) and short-circuits an identical re-upload: no second copy, no wasted Claude call,
   returns the existing record with a transient `duplicate` flag; the UI reports *"already
   uploaded (skipped)."* `backfill_hashes()` stamps the hash onto pre-dedup records.

**Done when:** you can upload a receipt on your phone and get a clean, approved record, in your
accountant's categories, with exact re-uploads caught. ✅ **Phase 1 complete.**

---

## Phase 2 — Statement import  ← building now

**Goal:** pull each account's transactions in from **any of four formats** — CSV, Excel,
PDF, or a phone **screenshot** — into one canonical transaction ledger, with a completeness
check so nothing is silently dropped.

**Design: one canonical shape, smart routing (approved 2026-06-20).**
- **CSV / Excel (.xlsx)** → AI detects the column mapping *once* (date / amount / description,
  signed-amount vs debit+credit columns, date format, opening/closing balance), then code parses
  **every row deterministically** — exact amounts, no dropped rows. (`csv` stdlib / `openpyxl`.)
- **PDF / screenshot / photo** → AI (`extract.build_content_block` + tool-use) extracts the rows
  directly — the only option for pixels.
- **Completeness gate (all sources):** two checks, whichever the source supports —
  (a) `opening + Σamount ≈ closing` when opening/closing balances are present (PDFs/CSVs); and
  (b) a **running-balance chain** check when each row carries a balance (screenshots, many CSVs):
  in order, each row's balance minus its amount must equal the next row's — a break means a
  likely missing row. Order-agnostic (screenshots are newest-first, CSV often oldest-first).
  This is the safety net that catches an AI-dropped/misread row before you commit.
- **Same-day identicals are NOT duplicates:** two equal same-day deposits (e.g. two Procom
  payments) are distinct — the **running balance** is part of the dedup key, and we never dedup
  *within* one statement (only across re-imports). Without this they silently collapse.
- **Account is user-selected** at upload (RBC / TD / Amex 1001 / Amex 1002 / Rogers) — reliable
  even when a screenshot hides the account number.
- **Sign convention:** negative = money out / charge / expense; positive = money in / refund /
  payment received. AI tags `direction` (expense / income / **transfer**), so card "PAYMENT
  THANK YOU" and inter-account moves are flagged transfers, not spend (the captured rule).
  Signs are decided by account kind: on a **credit card** the reader trusts the PRINTED sign, not
  the merchant, and flips it — purchases print positive → stored negative; **refunds/credits print
  negative → stored positive** (so `AMAZON.CA -395.44` is a $395.44 refund, not a purchase);
  payments → positive transfer. On a **bank** account it uses the printed sign (withdrawals already
  negative). The reconcile is sign-agnostic (asset balance rises with +amount; liability balance
  owed falls), so both reconcile cleanly. (Validated on 10 real Amex Cobalt statements; the
  reconcile gate caught a 7-refund mis-sign that would've overstated expenses by ~$1,500.)
- **Multi-file:** pick an account, select many files at once — each becomes its own import.
- **PDF repair:** some bank e-statements (Fiserv "PDF Export", e.g. Rogers) ship with a junk
  prefix before the `%PDF-` header (mis-detects as Java serialization) that the Anthropic API
  rejects as invalid. `business.repair_pdf()` rebuilds them with Ghostscript (fallback: trim to
  the embedded PDF) before parsing — wired into both statement and receipt ingest.
- **Dedup** on (account, date, amount, description) so re-imports don't double-count.
- **Review-before-commit:** an import lands as `pending` transactions under a reconcile banner;
  you fix/confirm, then **Commit** moves them to the committed ledger.

**Data area (under `business/`):**
- `statements_files/<import_id>.<ext>` — original uploaded file (audit trail).
- `imports.jsonl` — one record per uploaded file: account, source, balances, parsed vs stated
  count, reconciled flag, status.
- `statements.jsonl` — canonical transactions; `matched_receipt_id` stays `null` until Phase 3.

### Chunks
1. **`statements.py` core** — record shapes, storage, dedup key, reconcile math.
2. **Tabular reader** — CSV/XLSX load + AI `detect_columns` + deterministic parse.
3. **AI reader** — PDF/image `extract_transactions` (signed rows + balances + count).
4. **Ingest + server routes** — route by extension; `/api/business/statements*` (list/import/
   update/commit/reject/file), all passcode-gated.
5. **Web UI + nav** — `web/statements.html`: pick-account upload, reconcile banner, editable
   transaction table, dedup-skipped notice, Commit; **Business → Statements** nav link.
6. **Test** — synthetic CSV, XLSX, PDF, screenshot; verify each reader + reconcile + dedup;
   isolated where possible; back up + confirm user data intact.

**Privacy:** PDFs/screenshots go to Claude (same as receipts; your API key, not training).
CSV/Excel parse locally except the one-shot column detection.

**Done when:** you can upload a statement in any of the four formats for any account and get a
clean, reconciled, deduped set of transactions you commit. ✅ **Phase 2 complete** — validated on
real TD screenshots (48 txns, Feb–Jun 2026) + CSV; bugs fixed (same-day-identical collapse,
order-agnostic chain check, orphan-file cleanup).

---

## Fiscal year (cross-cutting config)  ✅ shipped

The corporation's fiscal year is configurable and respected across the books. Stored in
`business/config.yaml` (`fiscal_year_start_month`/`day`, **default Jul 1** = FYE Jun 30).
Engine in `business.py`: `fiscal_year_of(date)`, `fiscal_year_bounds(fy)`, labelled by the
**year it ends** and correct for any start incl. Jan 1 (calendar year) / Apr 1.
- **Config UI:** a **Corporation** card on Settings (month/day picker; shows derived FYE + current FY).
- **Statements:** every transaction/import is FY-stamped (computed at view time, so a config change
  reflows); a **fiscal-year filter** + per-import **FY badge**; **cross-FY imports flagged** (an
  import whose rows span Jun 30 is warned so each year's books stay separate).
- **Feeds:** Phase 4 HST periods (annual, aligned to FYE) and Phase 5 export are scoped by FY.

---

## Later phases

- **Phase 3 — Matching + reconcile UI:** receipt ↔ transaction (amount + date window + fuzzy
  vendor). **This is where the remaining dedup/grouping lives:**
  - **Near-duplicate receipts** (same purchase, *different* photo): the same fuzzy engine
    (vendor + date + |amount| within tolerance) flags likely repeats for one-tap merge.
  - **Receipt ↔ bank line = one economic event:** link, don't double-count — the `matches/`
    ledger is the single source of truth the export reads, each event counted once.
  - **Refund / return grouping:** detect the credit/negative line (or REFUND/RETURN/CREDIT on
    the receipt), link it to the original purchase, and **net** them for tax. Same family as the
    captured rule *"card PAYMENT = transfer, not expense"* → *"credit = reduce the expense."*
    Handles partial refunds and exchanges too.
- **Phase 4 — Categorize + HST/tax:** roll up into the chart of accounts; **HST collected vs
  ITCs** → net HST (annual, FYE Jun 30); meals 50% flag; **non-deductible add-back path** for
  fines/penalties (plain chart of accounts has no bucket for this); **FX→CAD** for foreign-
  currency receipts at transaction-date rate.
- **Phase 5 — Export:** accountant package (XLSX + summaries by category + HST net + missing-
  receipt list + zipped receipts), targeted at the FY2025 compilation format.

**Open decisions — all resolved** (from the FY2025 filing screenshots, 2026-06-19): HST
**registered (RT0001), Ontario 13%, filed annually**; **fiscal year-end June 30**; chart of
accounts captured; income = **cash basis / deposits**; scope includes mileage + CCA + occupancy;
5 accounts confirmed. Details in memory `corp-tax-profile` + `corp-chart-of-accounts`.

---

## Known limitation to harden later (data integrity)

`update_receipt` / `delete_receipt` / `backfill_hashes` do a **full-file rewrite** of
`receipts.jsonl`; uploads **append**. A simultaneous edit-save and upload could lose one write
(last-writer-wins). Low risk at single-user phone-upload volume, but worth a lock or
append-only-log + compaction before this is the sole tax archive. Always back up `business/`
before any batch write (as done for the dedup backfill).
