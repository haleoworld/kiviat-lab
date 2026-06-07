# Kiviat Lab — Design Direction (Phase 3 web app)

**Quality bar:** the Anthropic Console (platform.claude.com). Clean, dark, card-based,
phone-friendly, color used only to mean something. We adopt its visual grammar and adapt it
to our **confidence-first** principle.

Phone-first (the primary device is an iPhone over Tailscale), but must look right on desktop.

---

## Borrowed from the Console

1. **Card-per-metric tiles** — one big bold number per card; never crowded.
2. **Corner status pill** — small, colored, encodes status at a glance.
3. **Sparkline inside the card** — trend context without a full chart.
4. **Caption** under the number — a reference frame ("vs previous month").
5. **Filter pills row** at the top — scoping controls.
6. **Freshness stamp** — "Computed from N events · last upload <date>".
7. **Composition charts** — donut for breakdowns.
8. **Slim left icon rail** for navigation (collapses to bottom tab bar on phone).
9. **Restrained dark palette** — color reserved for meaning.

## The one adaptation: confidence-first

The Console leads each card with a **% delta**. We lead with **confidence**:

- The corner pill is a **confidence flag**: 🟢 high / 🟡 medium / 🔴 low.
- Trend (% vs last period) is *secondary* — small, optional.
- Every number is paired with `supporting_event_count` and `missing_data_flags`
  (the data behind `{value, confidence, supporting_event_count, missing_data_flags}`).
- A number with no backing data renders 🔴 with a "needs data" caption, never a confident 0.

---

## Screen → component map

**Filter pills (global, top of every screen):**
`Family: My Household ▾  ·  Member: All ▾  ·  Range: Last 3 months ▾`
The Family pill is the multi-tenant switcher — the most important control in the app.

**Home (survival dashboard)** — the hero screen, all cards confidence-flagged:
- `Liquid runway` — big "N months", confidence pill, sparkline of runway over time.
- `Stressed runway` — the "can we sleep at night" number (1 income lost + market −40%).
- `Net worth` — assets − liabilities.
- `Monthly burn` — with a mini split (survival floor / lifestyle / irregular).
- **Hero radar (Kiviat) chart** — the namesake: financial health across all dimensions
  (runway, burn discipline, allocation balance, risk exposure, savings rate) on one spider.

**Net Worth** — donut of allocation by asset class + assets/liabilities list.
**Burn Breakdown** — stacked/segmented bars: survival floor vs lifestyle vs irregular.
**Risks** — ranked list, each with a severity pill (severity × likelihood × reversibility).
**Review Queue** — pending events grouped by source file; one-tap approve/reject; mandatory-
  review items (low confidence / >$1,000 / anomaly) visually flagged and un-skippable.
**Upload** — drop a file, pick family + member, see extraction progress, land in Review Queue.

---

## Design tokens (starting point, tune in build)

- **Surface:** near-black background; cards a step lighter with a hairline border + rounded
  corners (~12–16px); generous padding.
- **Type:** one large bold weight for the metric number; muted gray for labels/captions.
- **Confidence colors:** green / amber / red — used ONLY for confidence + status, nothing
  decorative.
- **Charts:** sparklines (trend), donut (composition), radar (health overview). Minimal
  axes, muted strokes; the data is the hero, not the chrome.
- **Motion:** subtle; numbers can count-up on load; no gratuitous animation.

## Likely stack (per OVERVIEW)

React (Vite) + Tailwind for the look; a light chart lib (e.g. Recharts/visx) for
sparkline/donut/radar; FastAPI serving `/api/views/*` behind it. Served from the 24/7 Mac
mini, reachable by the family over Tailscale.

> Build this in **Phase 3** — after extraction is validated and Phases 1–2 (review/commit +
> computed views) exist to feed real numbers into these cards.
