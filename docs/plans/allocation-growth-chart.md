# Plan — Allocation growth-to-retirement mini chart + sticky metrics

**Status: ✅ SHIPPED (2026-06-22).** Backend: `retire` {current_age, retire_age} + `invest_base`
(asset_base − RE equity = $487,637) in /api/allocation. Frontend: `growthSeries`/`drawGrowth` line
chart + caption under the metrics strip, live-recurves in `updateDerived`; whole header
(title+metrics+chart+caption) wrapped in `position:sticky; top:0; z-20` block; IntersectionObserver
toggles `.stuck` → metrics shrink + chart grows 96→150px. **Income + growth re-based to the
investable $487,637** (real estate excluded), per user — income $1,898→$1,311/mo, growth →$6.3M@65.
Verified both states + computed sticky style. Only views.py + web/allocation.html.

Follow-up noted: the 3 "10%+ …" preset NAMES still encode old $706k-based $/mo (now ~31% lower);
none hit $1,500/mo on the investable base ($1,500/mo now needs 3.69% yield).

**Update (2026-06-23):** Added a **Nominal / Real toggle** on the growth chart. Backend adds
`inflation_pct` to the `retire` payload (from retirement inputs, default 3%). Real mode compounds
at `return − inflation` and labels "today's $". Default nominal. e.g. 11.1% nominal → $7.4M vs
8.1% real → $3.7M at 65.

## Goal
In the **"Your target mix"** card, below the live metrics strip, add a mini interactive chart of the
portfolio growing to retirement age. Pin the strip + chart (sticky) while that card is in view.

## Model (decided)
- Start = **allocation base $706,081** (same base as the income/metrics).
- Grow at the **mix's expected return** (live `metrics(targets).ret`, nominal) — recurves as the mix
  changes. **No contributions** (pure compounding), per user choice.
- Horizon = **current_age → retire_age** from retirement inputs (39 → 65 = 26 yrs).
- value(age) = base × (1 + ret/100)^(age − current_age). Caption shows end value "≈ $X.XM at 65".

## Chunks (~15 min)
1. **Backend** (`views.py`): add `retire` = {current_age, retire_age} to the `/api/allocation`
   payload (from `load_retirement_inputs`/`retirement_defaults`). (monthly_savings not needed now.)
2. **Frontend chart** (`web/allocation.html`): `growthSeries(ret)` → [{age,value}]; a Chart.js line
   `#mixGrowth` under `#mixLive`; redraw in `updateDerived` with current `t.ret`; tooltip = "age N:
   $X"; caption with end value. Falls back gracefully if no retirement age (hide chart).
3. **Sticky**: wrap `#mixLive` + chart + caption in a `position:sticky; top:0` block with solid card
   background + z-index, inside the mix card → pins under scroll until the card scrolls past.
4. **Test**: restart, headless-render; verify chart curve + caption, sticky pin while scrolling
   sliders, live recurve when mix/return changes, metrics unchanged.

## Notes
- Nominal return (not inflation-adjusted) — matches the rest of the allocation card. Can add a real
  toggle later.
- Only views.py + web/allocation.html change. No data writes.
