# Annual business reconciliation — repeatable process (SOP)

**Owner:** Terry. **Cadence:** once per fiscal year, after the year closes (FYE Jun 30).
**Goal:** every business expense/income transaction for the year is either backed by a
receipt or marked "no receipt needed", AND tagged with an accountant category — so the
books drop straight into the corp filing. Terry traces nothing; Claude drives and chases.

## Work order — by receipt-acquisition effort (easiest first)
The per-vendor "how do I get this receipt" profiles and the tier order live in
`data/families/<id>/business/reconciliation_playbook.yaml`. Work tiers top-down:
1 no-receipt fees → 2 portal/email PDFs (consistent) → 3 app/batch → 4 identify-first
(big $/unknown) → 5 manual scan (varied, tedious). Update the playbook as vendors are
learned so next year follows the same order. Key policy gate: `vehicle_method`
(mileage vs actual) decides whether gas/charging/toll need receipts at all.

## How Claude drives it
Claude is the engine + the nag. Interaction unit = **by vendor (+ that vendor's
category)**, worked in the playbook's effort order — never one transaction at a time. Each session leads with
"here's what's still open" and pushes the next vendor batch until everything is green.
**Single source of truth = the app's coverage state** (no separate tracker). Resume any
time by re-pulling coverage.

## The machinery (already built)
- `matching.coverage(family_id)` → every expense/income txn tagged
  `linked / suggested / missing / no_receipt`, each row carries its `category`;
  auto-suggests the best receipt by amount (±$0.02) + date (±5 days) + vendor overlap;
  lists **orphan receipts** (uploaded, matched to nothing); summary counts incl.
  `uncategorized`. Endpoint: `GET /api/business/coverage`.
- Confirm/clear a link: `POST /api/business/coverage/link` / `.../unlink`.
  **Confirming a link auto-inherits the receipt's category onto the txn** (never
  overwrites a manual one).
- Manual candidates: `GET /api/business/coverage/candidates/{txn_id}`.
- Set fields on a txn (category, no_receipt, …): `PUT /api/business/statements/txn/{tid}`.
  Category validated against the accountant's 11 expense + 2 income buckets
  (`business.ALL_CATEGORIES`).
- Coverage page UI: `/coverage` — status filters, clickable receipt links, per-row
  category dropdown, no-receipt toggle, candidate picker, orphan panel.

## Clickable links (give these to Terry)
Base = `https://jerrys-mac-mini.tailac0a52.ts.net/kiviat-lab`
- Receipt file: `…/api/business/file/<rid>`  · thumbnail: `…/api/business/file/<rid>/thumb`
- Coverage page: `…/coverage` · deep-link a vendor+year: `…/coverage?q=Rogers&year=2026`
- Reference docs panel: business page (`…/business`); files are FY-scoped under
  `business/reference/<FY>/` and served via `…/api/business/reference/file?year=<FY>&name=<file>`

## Receipt states (two independent axes)
- **reconciled/linked** = matched to its bank charge (coverage page).
- **approved** = the receipt's extracted data accepted (receipts review queue). Linking does
  NOT auto-approve — after verifying a vendor's receipts (incl. HST), approve them so they
  leave "To review".
- **Reference docs are saved per fiscal year**: `business/reference/<FY>/…` (don't dump them
  loose, or years mix).

## The phases (each pass)
0. **Snapshot** — pull coverage for the target FY; report terrain (suggested / missing /
   no-receipt / orphans + $).
1. **Confirm suggested** (fast wins) — by vendor, show amount/date + clickable receipt
   link; Terry ✓/✗; ✓ links it and auto-categorizes.
2. **Place orphans** — for each uploaded-but-unmatched receipt, propose the txn it
   belongs to; Terry confirms or marks personal/duplicate.
3. **Grind missing, by vendor + category** — cluster raw descriptions into vendors;
   propose the category for the whole group (Terry confirms/corrects once → applies to
   all); split each group into:
   - **receiptless-by-nature** (bank fees, interest, mileage, email-billed SaaS) →
     categorize + mark "no receipt needed";
   - **should-have-a-receipt** → chase Terry to upload; re-running coverage auto-matches.
4. **Close-out** — re-run coverage; target = 0 uncategorized, 0 missing-that-needs-a-
   receipt; hand Terry a punch-list of any receipts still owed. **Then total actual
   vehicle spend** (all Vehicle-expense txns: gas + Tesla charging + 407 + service) and
   write it to `vehicle_actuals_total` in the playbook, so Terry can compare it to his
   mileage-allowance value and top up personal→corp for any excess (taxable-benefit
   avoidance). Confirm the booking treatment of that top-up with the accountant.

## Categorization hints (accountant buckets — see memory corp-chart-of-accounts)
fuel/gas/parking/taxi/407ETR/auto → Vehicle expense · restaurant/coffee/food → Meals and
entertainment · client gifts (Costco/Shoppers) → Advertising and Promotion · SaaS/AI
subscriptions (OpenAI, xAI, Google, Spotify, Bell, internet) → Internet or Office
expenses per accountant · bank/interest/FX fees → Bank charges · card payment = transfer
(not an expense) · client invoices (Procom AP deposits) → Revenue.

## Scope notes
- Reconcile **one FY at a time**; confirm scope at the start (FY2026 done first).
- Income deposits (Procom AP) → mark Revenue; they don't need a "receipt" unless the
  accountant wants the issued invoice attached.
- Credit-card payments / transfers are `direction: transfer` → excluded from the need set.

## Status log
- 2026-06-26: built txn-level categories (statements `_EDITABLE`+validation; coverage rows
  carry category + `uncategorized`; link inherits category; `/coverage` per-row dropdown).
  FY2026 baseline: 74 suggested, 311 missing, 58 no-receipt, 27 orphans, 480 uncategorized.
  Started Phase 1 (suggested): caught 2 false matches (PRESTO transit ↔ same-priced Shell
  receipts); 72 clean awaiting confirm. Paused.
- 2026-06-26: vehicle policy = mileage_with_actuals (keep gas/charging/407 receipts; total
  actuals at close-out for the personal→corp top-up). Saved per-vendor playbook
  (`reconciliation_playbook.yaml`, effort-tiered order).
- 2026-06-26: surfaced playbook + SOP + reasoning in-app as a read-only "Reconciliation"
  card on Settings (gated to business households). Backend
  `GET /api/business/reconciliation`; store-and-display only (no behavior change).
- 2026-06-26: added read-only "Reference documents" panel on the business page
  (`GET /api/business/reference` + file serve w/ traversal guard) and `?q=`/`?status=`
  deep-link prefilter on the coverage page (e.g. /coverage?q=Rogers).
- 2026-06-26: **Rogers DONE (Tier 2)**. 11/11 FY2026 charges linked to invoice PDFs,
  all categorized Internet. HST verified per-bill (extractor had misread several):
  Jul–Oct $7.36, Nov $7.26, Dec–May $6.96 → FY2026 Rogers HST ITC = $78.46 (+$6.96 when
  June imports). 4 account screenshots saved under business/reference/. Jun-2026 invoice
  parked as orphan (links when June bank stmt arrives ~July); Jun-2025 (prior filed FY)
  discarded. Lesson: telecom $62.15 months carried a post-tax discount, so HST ≠ 13% of
  total — always verify HST against the PDF, don't compute it.
