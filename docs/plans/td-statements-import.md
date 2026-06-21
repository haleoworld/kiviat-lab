# Plan — Replace partial TD screenshots with full validated PDF history

**Status: ✅ SHIPPED (2026-06-21).** 124 PDF txns imported as 13 reconciled imports; 5 June
screenshot txns kept (user choice); 43 superseded screenshots removed. All 13 reconcile + chain
continuous ($108,286.95→$47,068.50). Backups: `/tmp/kiv_statements_<ts>.jsonl`,
`/tmp/kiv_imports_<ts>.jsonl`. Directions rule-based (user refines on Statements page).

## Goal
Replace the 48 partial TD **screenshot** transactions with **124 fully-validated** transactions
parsed deterministically from 13 monthly PDF statements (**Apr 30 2025 → May 29 2026**), giving a
complete, reconciled, continuous TD Bank history.

## Validation (done, read-only — `/tmp/td_parsed.json`)
Deterministic `pdftotext -layout` parser (no AI/API cost). Column anchors: debit ends ~119–124,
credit ~158, balance ~208. Year inferred from each statement's period endpoints. **All 13:**
- reconcile (opening + Σcredits − Σdebits = closing), **and**
- match the bank's printed Credits/Debits **count + amount** checksum, **and**
- chain continuously (each closing = next opening; all 12 boundaries ✓).
124 txns, 0 discrepancies.

## Existing data is safe to replace
48 TD screenshot txns, all `committed`: **0** receipt matches, **0** categories, **0** reconciled;
only **7** `no_receipt` flags (fees/tax) — re-derived by rule. 7 screenshot import records.

## Direction rules (rule-based, free; user refines on Statements page)
- **transfer:** AMEX CARDS · ROGRS BNK · INTERACTIVE · SEND E-TFR · GC 0500/DEPOSIT
- **income:** credits not matched above (Procom AP, ACCT BAL REBATE)
- **expense:** debits not matched above (fees, restaurants, tax remittances, cheques)
- **no_receipt auto-mark:** MONTHLY PLAN FEE, TAX PYT FEE, GST-P, GST-B, TXINS, TXBAL, ACCT BAL REBATE
- Sign: debit → negative amount, credit → positive. Per-txn running balance preserved where printed.

## Execution chunks (~15 min)
1. **Back up** `transactions.jsonl` + `statement-imports.jsonl` → `/tmp/kiv_*_<ts>`.
2. **Remove** the 48 `TD Bank` txns + their 7 import records from the ledger.
3. **Insert** 124 txns as **13 import records** (one per statement, like Amex/Rogers): status
   `committed`, opening/closing/reconcile meta, `dedup_key`, FY-stamped, directions per rules.
4. **Re-apply** `no_receipt` per the rule above.
5. **Verify:** reload; re-run reconcile + chain check from the stored ledger; confirm 124 TD txns;
   confirm Finances / Coverage / Statements pages load; spot-check a few directions.

## Open decision
- **June screenshot tail** (5 txns, Jun 1–15 2026, beyond PDF coverage): DROP (re-import when the
  June statement is downloaded — keeps everything reconciled) vs KEEP as-is (unreconciled gap).
