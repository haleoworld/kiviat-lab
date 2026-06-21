# Plan — DCA Planner (no-brainer per-class contribution page)

**Status: ✅ SHIPPED (2026-06-21).** All 5 chunks built + restart-tested; numbers cross-checked
(crypto weekly/12mo → CAD $142 ≈ US$104), targets rescale to 100%, settings persist. No existing
data files touched (`dca.yaml` is new, family-scoped).

## Goal
A page that, for every asset class, tells the user **how much to contribute per period** (at a
frequency they set) to slowly reach their **target allocation %** — so when the time is right for a
class (e.g. crypto in a bear market) they DCA that number with no math. One class at a time.

## Decisions (locked)
- **Base = liquid only.** Exclude real-estate book equity (frozen, can't DCA). `T_liq = full_base −
  RE_equity`. Targets **rescaled** across the movable classes to sum 100% (drop `real_estate`'s 5%,
  divide each remaining target by 0.95). Real estate shown separately as info, not in the mix.
  - At build time: full base $706,081 − RE $218,444 = **T_liq $487,637**.
- **Single-class self-dilution formula:** to land one class on target by adding only to it,
  `C = T_liq × (target_frac − current_frac) / (1 − target_frac)`; current/target are fractions on
  the **liquid** base. Per-period = `C / periods`, `periods = periods_per_year(freq) × months / 12`.
- **Overweight classes** (`C ≤ 0`): render as amber "overweight — trim or hold; new money to other
  classes dilutes it down." No DCA-in number.
- **Controls:** one **global frequency** + a **per-class timeframe in months** (default 12,
  editable per row). Persisted.
  - `freq → periods_per_year`: twice-weekly 104 · weekly 52 · biweekly 26 · monthly 12.
- **Dual currency:** each per-period amount shown in **CAD and USD-equivalent**
  (`USD = CAD / fx_usd_cad`, rate from app config), since the user holds some USD.
- Suggestions are "if you start today" — recomputed each load from live holdings/snapshot.

## Worked example (crypto, weekly, 12 mo, at build-time data)
current 1.16% → target 2.63% on liquid base → C = $7,378 → **$142/week** over 52 weeks.

## Build chunks (~15 min each)
1. **`views.dca_plan(family_id)`** — compute liquid base, rescaled targets, per-class current%,
   `C`, and per-period using saved settings. Returns rows (key,label,current_pct,target_pct,gap$,
   per_period_cad, per_period_usd, direction add/trim/ontarget), plus `base`, `frequency`,
   `fx_usd_cad`, real-estate info row.
   Reuses `allocation()` + `allocation_calculator()` (no recompute of holdings logic).
2. **`views.load_dca_settings` / `save_dca_settings`** — `data/families/<fid>/dca.yaml`:
   `{frequency: weekly, timeframes: {<key>: months}}`. Defaults: weekly, all 12.
3. **Server routes** — `GET /api/allocation/dca` (plan), `PUT /api/allocation/dca` (settings).
4. **`web/dca.html`** — table per chunk-1 shape; global frequency selector (twice-weekly/weekly/
   biweekly/monthly); per-row months input (auto-save, debounced, like other pages); add=green
   $/period shown **CAD + USD-equiv**, trim=amber note, on-target=grey.
   Header shows liquid base + a one-line "real estate $X (30.9%) set aside" note.
5. **Nav** — add "DCA Planner" under Planning (after Allocation) in `web/_nav.html` + active-highlight.

## Test / safety
- No writes to existing data files; `dca.yaml` is new and family-scoped. Verify with headless Chrome
  after restart (Python change → `launchctl kickstart -k`). Cross-check chunk-1 numbers against the
  table in this plan (crypto $142/wk). Confirm rescaled targets sum to 100% across movable classes.
