# Add another household + shared allocation-preset library

**Status:** Track A done. Nav-persistence fix done. Track C (business gating &
scoping) done & committed — chunks 1-4. Per-household accounts: planned, awaiting
go-ahead. Track B (shared preset library) DONE & committed (094c46a, 3fa53c3). (2026-06-24)

## Track B — global shared allocation-preset library (DONE)
Global data/allocation_presets.yaml; preset = {name, targets, stats, borrow}.
views: load/save/add/delete_alloc_preset + adopt_alloc_preset (copy snapshot into a
household's plan — replaces targets/stats/borrow, preserves the household's mixes).
server: GET/POST/DELETE /api/allocation/presets + POST .../presets/adopt.
allocation.html: "Preset library" card — list w/ metrics, Adopt, delete, Save to
library. Verified: save→list→adopt→edit-household leaves preset unchanged (snapshot,
no live link); cross-household isolation holds.

## Track C — gate & scope the business module (DONE, commits 3378dd6..5955ff5)
- has_business signal: flag in family.yaml, inferred true for families with existing
  business data (household auto-recognized). /api/families reports it;
  POST /api/business/enable opts a family in + scaffolds config.
- Nav: BUSINESS group wrapped in #navBiz, hidden unless selected household has a
  business; drawer header shows selected household; kiviatRefreshNav() refetches so
  switch/enable update live.
- Settings: Corporation card gated; "Add a business / corporation" button enables it.
- Business pages (inbox/business/statements/coverage): resolve household from
  sessionStorage (was: always active family) — closes the receipt leak.
- Verified: API isolation (sister 0 / household 105), enable flips flag + scaffolds,
  all JS parse-clean. Browser visual confirm pending with Terry.

## Per-household accounts (DONE, commits f70d6f2, be0142a)
Backend: accounts + credit_card_accounts per family in business config; legacy
fallback for pre-change families; household migrated into its own config; new
business families scaffold EMPTY accounts. Settings UI: chips show type/archived,
"+ Add account" (name + bank/credit), full round-trip save. Verified: household
keeps its 5 (typed), new family empty, add persists, cross-family isolation holds.

## Per-household accounts — original plan (PLANNED — kills global statements.ACCOUNTS)
Decisions: archive-only (never hard-delete; statements store account NAME → deletion
orphans history); migrate household's 5 accounts into its business config so they
become editable per-family data; new business families start with EMPTY accounts;
each account carries a bank/credit type (drives account_kind/HST).
Chunks: (1) backend: accounts+credit list in business config w/ legacy fallback,
update active_accounts/account_kind/validation/overview + inbox import check;
(2) settings UI: add account (name+type), archive/unarchive, list per family;
(3) verify isolation + import still works.

## (Original Track A/B notes below)
**Requested by:** Terry. **Why:** plan finances for separate, strictly-isolated households
(sister now; parents later) while reusing saved allocation plans across them.

## Key finding (the reframe)
Multi-household is already first-class in the app:
- `families.py create <id>` + per-page household switcher already exist; data auto-scopes
  per family under `data/families/<id>/`. Isolation is already strict.
- "Active preset" today = a UI label flagging which *saved mix* matches the live sliders.
  It is **not** a library.
- No global/shared storage exists yet. Saved "mixes" live per-household inside
  `allocation_plan.yaml` (name + target weights only; no assumptions).

So the request splits into two tracks of very different size.

## Decisions locked
- **Strict isolation** per household; each household's *active* plan is private.
- **Adopt = one-time copy (snapshot)**, not a live link. Edits to a global preset do not
  propagate into households that already adopted it.
- New household id/name: `sister` / "Sister".
- Create **without `--activate`** (keep `household` as the default active family).
- **No seeding** of sister's allocation plan: the allocation page already falls back to
  researched defaults, and copying Terry's weights would impose his risk mix on her.
  Track B (adopt) is the proper way to start from a saved plan.

## Track A — Create the household · ~15 min · ships today
- **A1:** Back up `data/` (done). Run `families.py create sister --name "Sister"`.
  Verify only `data/families/sister/{family.yaml,members.yaml}` + dirs are written and
  `household` data is untouched; `app-config.yaml active_family` stays `household`.
- **A2:** Headless-Chrome verify: `sister` appears in the switcher on every page; her
  allocation/finances/assets pages load (defaults, working); switching back to `household`
  shows Terry's data intact.

## Track B — Global shared preset library · ~4 chunks · separate session
Preset shape: `{name, targets, stats, borrow}` (weights **and** assumptions bundled).
- **B1 (~15m):** Global store `data/allocation_presets.yaml` + load/save/list helpers
  (`paths.py` / `views.py`). No household data touched.
- **B2 (~15m):** Endpoints — `GET /api/allocation/presets` (list), `POST .../adopt`
  (copy preset into current household's plan), `POST .../save` (publish current household
  plan to the global library).
- **B3 (~15m):** Allocation-page "Preset Library" panel: list presets w/ metrics, **Adopt**
  (copy → then editable), **Save to library**.
- **B4 (~10m):** Tests + headless verify adopt→edit→isolation loop; sync docs.

## Verification standard (every chunk)
Lint + tests pass; app works headless (per kiviat-dev-workflow); never clobber user YAML;
get Terry's approval before any commit.
