# Kiviat Lab — Overview

> **One sentence:** Create a family, add its members, drop in their financial documents;
> AI reads each one into a list of facts; you approve those facts; a phone dashboard shows a
> few survival-critical numbers for the **currently selected family** — and every number
> tells you how much to trust it. Original files auto-delete after ~2 weeks; the extracted
> facts live forever.

The name fits the mission: a **Kiviat diagram is a radar / spider chart** — the natural way
to show a family's financial health across several dimensions (runway, burn, allocation,
risk) at a single glance. This repo is the lab that produces those readings.

This is the front-door document. For setup and how to run the current build, see
[`README.md`](README.md).

---

## Two ideas everything hangs off

**1. Events, not totals.** The system never stores a number like "net worth = $X." It stores
**immutable facts** ("on May 1, a $2,900 mortgage payment left Main Chequing"). Every number
on screen is *computed live* by summing those facts. Nothing displayed is ever stale, and any
number traces back to the exact line on the exact statement it came from.

**2. Confidence is first-class.** No number ever appears naked — it's always 🟢 high /
🟡 medium / 🔴 low. A runway built from one fuzzy screenshot looks different from one built on
six clean statements, and the dashboard says which.

---

## Multi-tenant by design: families & members

Kiviat Lab is **multi-family**. You can run several families (your household, your parents'
household, etc.), each with its own members, documents, event log, and dashboard. A "family
switcher" picks the active one; every metric is scoped to that selection.

```
Family ("My Household")
  ├── Member (Alice)        accounts/docs can be tagged to a member…
  ├── Member (Bob)
  └── Events                …or left at the family level
Family ("Parents")
  └── …
```

- **Family** — the top-level tenant. Has its own household facts, thresholds, stress-test.
- **Member** — a person in a family. Tagging an event to a member is *optional* (joint
  accounts stay at the family level).
- Every event carries a required `family_id` and an optional `member_id`.
- The extraction engine itself is tenant-agnostic — a statement is a statement. The family
  dimension is chosen at upload time and stamped onto the events.

---

## The main flow (the happy path)

```
  👪 Pick / create a family   →   families/<id>/
  📄 Drop a file              →   families/<id>/inbox/
  🤖 AI reads it,             →   families/<id>/staging/   (events "pending")
     extracts facts                tagged family_id (+ optional member_id)
  👀 Review & approve         →   families/<id>/events/    (the permanent log)
  📊 Dashboard recomputes     →   phone shows that family's survival numbers
  🗑️  Original file            →   deleted after ~15 days
     auto-deletes                  (but a text snippet of each fact is kept forever)
```

---

## The five layers

| Layer | What it does | Status |
|---|---|---|
| **1. Capture** | A per-family `inbox/`. Accepts any format (PDF, JPG, PNG, HEIC, TXT, MD). | folders exist |
| **2. Extraction** | Detects a new file, sends it to Claude, writes tagged events → that family's `staging/`. | **built (Phase 0)** |
| **3. Review & Commit** | Shows pending events grouped by source file; you approve/reject; approved events move to the append-only log. | not built |
| **4. Computed Views** | Pure functions turn one family's event log into the dashboard numbers. | not built |
| **5. Web App** | A phone-first dashboard with a **family switcher**, reached over Tailscale. | not built |
| **Retention** | Daily job deletes inbox files older than `retention_days`; snippets persist in events. | not built |

---

## The features that matter (the dashboard)

A **small, fixed** set of survival-first answers per family — not infinite charts:

- **Liquid runway** — months you survive on cash-like assets alone.
- **Stressed runway** — same, but assuming one income vanishes *and* markets drop 40%.
  (The "can we sleep at night" number.)
- **Monthly burn** — split into survival floor / lifestyle / irregular.
- **Net worth** — assets minus liabilities.
- **Allocation** — what the money is sitting in, by asset class.
- **Ranked risks** — top worries, scored by severity × likelihood × reversibility.

Each returns `{ value, confidence, supporting_event_count, missing_data_flags }`. Together
they're the spokes of the Kiviat/radar readout.

---

## The data model

```
kiviat-lab/data/               ← the DATA root (gitignored; override w/ KIVIAT_DATA_ROOT)
  app-config.yaml              global settings (model, retention, active family)
  .env                         ANTHROPIC_API_KEY
  families/
    <family-id>/
      family.yaml              this family's name, household facts, thresholds, stress-test
      members.yaml             the people in this family
      inbox/                   raw uploads, auto-deleted after N days
      staging/                 extracted events awaiting review
      events/                  committed log (append-only JSONL, one file per month)
      notes/                   freeform markdown
```

**Event shape** (one financial fact):

```json
{
  "id": "uuid",
  "family_id": "household",
  "member_id": "alice",
  "date": "2026-05-15",
  "account": "Main Chequing - Personal",
  "type": "transaction | holding_snapshot | balance_snapshot | bill | note",
  "description": "MORTGAGE PMT - PROP 1",
  "amount": -2900.00,
  "currency": "CAD",
  "category": "mortgage_principal_interest",
  "confidence": "high | medium | low",
  "source_file": "2026-05-chequing.pdf",
  "source_snippet": "May 01 MORTGAGE PMT 2,900.00",
  "extracted_at": "2026-05-30T12:00:00Z",
  "review_status": "pending | approved | rejected",
  "reviewer_notes": null
}
```

`member_id` is `null` for joint / family-level facts. The model fills the financial fields;
the program stamps `id`, `family_id`, `member_id`, `source_file`, `extracted_at`,
`review_status`, `reviewer_notes`.

---

## The non-negotiable rules (the constitution)

1. **Flexible input, opinionated output** — eat any file format, produce only the fixed set
   of views. (The guardrail against infinite scope.)
2. **Events, not totals** — extract immutable facts; all displayed numbers are computed views.
3. **Confidence is first-class** — never display a number without a confidence flag.
4. **Append-only history** — corrections are *new* events; never mutate the past.
5. **Review before commit** — AI extractions are guilty until approved; nothing hits the
   permanent log unreviewed.
6. **Mandatory review triggers** — force a look at anything low-confidence, over $1,000, or
   more than 2× its category median.
7. **Local-first, no cloud** — everything runs on the Mac mini. The only thing that leaves
   the machine is the document sent to Claude for reading. No Plaid, no bank APIs.
8. **Snippets are the audit trail** — when a source file auto-deletes, a verbatim text
   fragment stays attached to the event forever.
9. **Tenant isolation** — a family's events, dashboard, and documents never bleed into
   another's; every fact is scoped by `family_id`.

---

## Why the design is shaped this way (failure modes it dodges)

- **Auto-delete could make AI mistakes permanent** → review-before-commit + the kept snippet.
- **"Any format, any category" could balloon forever** → output locked to a fixed set of views.
- **The system dies if uploading is a chore** → the ritual must stay under ~15 min/month.
- **The Mac mini is a single point of failure** → it must run 24/7, ideally degrading to
  read-only if down.
- **Burn rate is unverified from memory** → the dashboard flags it 🔴 LOW until real
  statements arrive.

---

## Scope guardrails — what v1 will NOT do

No Plaid / bank-API integrations · no live market data (manual refresh) · no native mobile
app (web over Tailscale is enough) · no multi-currency conversion (CAD-first) · no tax
*computation* (flag implications, don't calculate) · no user-configurable views · no public
internet exposure.

---

## Tech stack (chosen for simplicity, not novelty)

- **Language:** Python 3.11+ (runs on 3.9 too)
- **Backend:** FastAPI + Uvicorn
- **Frontend:** React (Vite) + Tailwind, phone-first
- **Storage:** plain files (JSONL events, YAML config) — no database in v1
- **File watching:** `watchdog`
- **AI:** Anthropic Claude API for extraction
- **Networking:** Tailscale (private, no public exposure)
- **Process mgmt:** `launchd` for auto-start on the Mac mini

---

## Build sequence

- **Phase 0 — Extraction pipeline** ✅ *built (multi-family aware), awaiting validation on real documents*
- **Phase 1 — Review & event log** — `review.py` (approve/reject) + `commit.py` (staging → events)
- **Phase 2 — Computed views** — `views.py` survival math + `report.py` markdown dashboard
- **Phase 3 — Web app** — FastAPI API + React phone-first UI with the family switcher
- **Phase 4 — Automation** — file-watcher daemon, daily retention cleanup, `launchd` plist

**The gate:** extraction quality is the whole product. Phase 0 must be trustworthy on
several real documents — iterating on [`prompts/extraction_prompt.md`](prompts/extraction_prompt.md) —
before any later phase is built. Building the web app on a flaky extractor means debugging
the wrong layer for weeks.

---

## Honest caveat

This system surfaces risks, runs math, and ranks action items — but it makes you
**informed, not advised**. Mortgage strategy, corporate tax planning, big property
decisions, and insurance choices should be validated with a fee-only advisor and a CPA. It is
not a substitute for a licensed professional.
