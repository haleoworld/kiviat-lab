# Kiviat Lab

Local-first, AI-powered family finance system. Create families and members, drop their
statements/screenshots/notes into a per-family inbox; Claude extracts structured events; a
phone-friendly dashboard renders survival-first numbers with confidence flags, scoped to the
selected family. Source files auto-delete; extracted data persists.

A **Kiviat diagram is a radar/spider chart** — the dashboard's job is to plot a family's
financial health across several dimensions at a glance. See [`OVERVIEW.md`](OVERVIEW.md) for
the full picture.

**Status: Phase 0 (extraction pipeline, multi-family aware) — built, awaiting validation on a real statement.**

---

## Layout

Code and data live in **one project folder**. The `data/` subfolder holds everything
private and is **gitignored** (never committed). Override its location with `KIVIAT_DATA_ROOT`.

```
kiviat-lab/                      ← this project (the CODE — publishable)
  extract.py  families.py  config.py  paths.py
  prompts/extraction_prompt.md
  README.md  OVERVIEW.md  requirements.txt
  data/                          ← the DATA (gitignored — never committed)
    app-config.yaml              global settings (model, retention, active family)
    .env                         ANTHROPIC_API_KEY
    families/
      <family-id>/
        family.yaml              name, household facts, thresholds, stress-test
        members.yaml             the people in this family
        inbox/                   raw uploads (auto-deleted later)
        staging/                 extracted events awaiting review   ← Phase 0 writes here
        events/                  committed append-only log          (Phase 1+)
        notes/                   freeform markdown
```

## Setup (one time)

```bash
cd ~/developments/claude/kiviat-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then add your Anthropic API key — edit `data/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

(Get a key at https://console.anthropic.com/settings/keys)

## Manage families & members

```bash
python families.py list
python families.py create household --name "My Household" --activate
python families.py add-member household alice --name "Alice" --role earner
python families.py members household
python families.py activate household        # set the default family
```

A starter family `household` already exists (migrated from the original config).

## Phase 0 — extract one file

```bash
source .venv/bin/activate
# uses the active family by default:
python extract.py ~/path/to/a-real-statement.pdf
# or target a family / member explicitly:
python extract.py ~/path/to/statement.pdf --family household --member alice
```

Supported: PDF, JPG, PNG, HEIC, TXT, MD.

It prints a summary (one line per event with a 🟢/🟡/🔴 confidence flag) and writes the full
result to `data/families/<family>/staging/YYYY-MM-DD-<hash>.json`. Each event is
tagged with `family_id` and (if given) `member_id`.

### Validation gate

Run it on **one real statement**, open the staging JSON, and check:
- Did it catch every transaction / holding?
- Are amounts and signs correct (money out negative, money in positive)?
- Does each event have a verbatim `source_snippet`?
- Are confidence flags honest (unclear things marked 🟡/🔴, not 🟢)?

If extraction is wrong, the fix is almost always in **`prompts/extraction_prompt.md`** —
edit it and re-run. The prompt is the actual product; everything else is plumbing.

**Do not proceed to Phase 1 until extraction is trustworthy on a few real documents.**

## Privacy — safe to make public

The **code** is safe to publish; the **`data/` folder is not** — it holds your API key,
income, members, and statements. The safety boundary is git itself:

- Everything except `data/` is generic, no secrets. ✅ publishable.
- `data/` is listed in [`.gitignore`](.gitignore), so git never tracks or pushes it. ❌

**The rule that keeps this true:** never force `data/` into git (e.g. `git add -f data/`).
To confirm it's safe before any push, run `git status` — you should see no `data/` files
listed, and `git check-ignore data/` should print `data/`. If you'd like to version your
event log, do it in a **separate private repo**, never this public one.

Example account names in the code/prompt (`Main Chequing`, `Brokerage Margin`) are generic
placeholders, not real institutions.

## What's NOT built yet

Phases 1–4 (review/commit, computed views, web app, automation) are intentionally unbuilt
pending Phase 0 validation. See [`OVERVIEW.md`](OVERVIEW.md) for the full plan.
