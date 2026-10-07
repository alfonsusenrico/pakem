# Design Base: owner-dashboard

Status: Draft (pending owner review)
Applies to: data dashboards and analytics consoles (tool surface)
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner portfolio (primary; rules and discipline), cash-flow-tracker web UI, office data dashboard and office task workbench web UIs (UI study 2026-10-07); dataviz reference categorical palette; W3C WCAG 2.2

Calm, light-first analytical console: cool neutral canvas, white working surfaces, navy
ink, one quiet blue accent, and a semantic triad that appears only for real state. The
page reads top-down as an argument (conclusion, then what to do, then the numbers, then
the evidence) in dense but aligned type with tabular figures. Hierarchy comes from
alignment, spacing, and 1px hairlines; the surface is flat, the dark theme is a calm
navy rather than black, and nothing glows, shimmers, or celebrates.

## Tokens

Neutral, semantic, and focus tokens are shared with `owner-workbench` so one product can
host both surfaces. Values marked "(default; project may change)" are recommendations
from the evidence; re-measure contrast after any change.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Canvas | `--canvas` | `#f4f6f9` | `#101926` |
| Surface | `--surface` | `#ffffff` | `#182334` |
| Subtle surface (hover, raised overlay in dark) | `--surface-subtle` | `#f8fafc` | `#1e2b3e` |
| Ink | `--ink` | `#172337` | `#edf2f8` |
| Secondary ink (labels, column headers) | `--ink-secondary` | `#475569` | `#c3cedb` |
| Muted (meta, axis ticks, placeholders) | `--muted` | `#5b6b7f` | `#a8b5c6` |
| Line (hairline dividers) | `--line` | `#dfe6ee` | `#2c3b4f` |
| Line strong (header rules, group edges) | `--line-strong` | `#cad5e2` | `#3b4c62` |
| Control border (inputs, checkboxes; >=3:1) | `--control-border` | `#7a889b` | `#75879e` |
| Accent: interactive only, chosen per project (default; project may change) | `--accent` | `#1769d2` | `#78b3f3` |
| Accent hover | `--accent-hover` | `#1156b1` | `#8abcf6` |
| Accent soft tint (selected row, user bubble) | `--accent-soft` | `#eaf3ff` | `#193d68` |
| Text on accent | `--on-accent` | `#ffffff` | `#0b1a2e` |
| Focus ring | `--focus-ring` | `#1769d2` | `#78b3f3` |
| Success / container | `--success` / `--success-container` | `#257b55` / `#edf8f2` | `#76c99c` / `#1d3c31` |
| Attention / container | `--attention` / `--attention-container` | `#9a5b00` / `#fff7e7` | `#e5ad57` / `#3c301c` |
| Critical / container | `--critical` / `--critical-container` | `#b42318` / `#fff0f0` | `#ee8585` / `#442729` |
| Chart tooltip, inverted (default; project may change) | `--tooltip-bg` / `--tooltip-ink` | `#172337` / `#ffffff` | `#edf2f8` / `#172337` |
| Overlay scrim | `--scrim` | `rgb(16 25 38 / 42%)` | `rgb(4 8 14 / 60%)` |

Chart series roles (marks only; text stays in ink tokens):

| Role | Light | Dark |
| --- | --- | --- |
| Primary series | categorical 1 | categorical 1 |
| Comparison (previous period, baseline) | `#7a889b` solid | `#75879e` solid |
| Target | `--ink`, dashed 4 4 | `--ink`, dashed 4 4 |
| Forecast or projection | same hue as its actual series, dashed | same |
| Categorical 1-8 (fixed order, never cycled) | `#2a78d6` `#eb6834` `#159a6b` `#b98200` `#d5638e` `#008300` `#4a3aa7` `#e34948` | `#3987e5` `#d95926` `#199e70` `#c98500` `#d55181` `#008300` `#9085e9` `#e66767` |

- Use at most 6 categorical slots; fold the rest into "Other" or small multiples. Slots 6
  (green) and 8 (red) are never used in a chart that also shows status.
- Sequential scales use one hue light to dark; diverging scales use two hues with a
  neutral grey midpoint. Never a rainbow, never a dual axis.
- Every chart with two or more series has a legend, direct labels for up to four series,
  and a table view; identity is never colour alone.

Provenance: neutrals and the dark theme from the office task
workbench (`src/web-ui/src/teacher-desk.css`, `--desk-*`), with muted and control border
darkened to pass contrast; semantic triad and tonal-container model from the office data
dashboard (`src/web-ui/src/design-tokens.css`, `--status-*`), with light values darkened
to pass contrast; accent from the workbench blue (light) and its rail indicator (dark);
inverted flat tooltip from the office data dashboard; categorical palette from the
dataviz reference palette (already used by the office task workbench for its first three
slots), with light slots 3-5 stepped darker to reach 3:1 and re-validated (adjacent CVD
delta E 7.3 light, 8.4 dark; secondary encoding is therefore mandatory). Neutral
temperature is cool because both office tools agree (default; project may change); a
project may instead derive warm neutrals from `owner-studio` (cream and navy) if it
re-measures every pair.

## Type

- Family: system stack `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
  Arial, sans-serif` (default). A project may self-host one family when it strengthens
  identity (at most two families, only the weights used); the portfolio's DM Sans is the
  suggested owner choice when one is wanted. Confirm the face has tabular
  figures (`tnum`) before adopting it.
- Weights: page and section headings 600; KPI values 600; body 400; text of 12px and
  below 500-600. Never 800 or heavier; nothing below 400.
- Scale (rem at a 16px root): caption and meta 0.75rem; body and table cells 0.875rem;
  section title 1rem; page title 1.25rem; KPI value 1.75rem with line height 1.15. Body
  line height 1.5; prose blocks (summary, notes) 1.6 at 60-78ch, left-aligned.
- `font-variant-numeric: tabular-nums` on tables, KPI values, chart axes, and deltas;
  numeric columns right-aligned.
- Tracking 0 for UI text; uppercase table and field labels +0.05em (design base rule).
- Uppercase only for table column headers and field labels: 0.75rem (never below 11px),
  weight 600, `--ink-secondary`. Sentence case everywhere else.

Provenance: weight, uppercase, and tracking rules from the owner design base (research
cited there); tabular figures from the office data dashboard; 14px body
from cash-flow-tracker `src/app/globals.css` and the office data dashboard scale.

## Spacing and density

- 4px base: 4, 8, 12, 16, 24, 32, 48. Gaps: 16 inside a group, 24 between groups, 32
  between page sections.
- Control height 36px with pointer: fine; 44px with pointer: coarse. Targets never below
  24x24.
- Table rows 40px (dense) or 48px (comfortable); cell padding 8 by 12.

## Layout

- Shell: sidebar 260px expanded, 72px collapsed (collapses at 1023px and below), on
  `--surface` with a right hairline; top bar 56px with page title, period, and theme
  toggle; filter toolbar directly below, sticky while the page scrolls.
- A data-source line under the toolbar ("Source, updated time") in `--muted`, with no
  alert styling unless the data is stale or partial.
- Page order: conclusion (summary or brief), then actions (attention queue), then 3-5
  KPI values, then evidence (charts and tables), then drill-down (detail route or
  drawer).
- KPI row: `repeat(auto-fit, minmax(180px, 1fr))`; figures separated by hairlines inside
  one surface rather than boxed tiles; a tile is a card only when it opens a drill-down.
- Context belongs next to the content it describes; no detached right-side context rail.
- Container queries inside panels and chart groups; media queries only for the shell.

Provenance: order and shell from the office data dashboard (`src/web-ui/src/styles.css`,
its material-console spec); sidebar 260/72 and top bar 56 from cash-flow-tracker
`components/layout/Sidebar.tsx` (the office dashboard's 292/76 is wider than needed; the
220px `--sidebar-width` in `tokens.css` disagrees with the rendered 260px sidebar).

## Shape

- Controls (buttons, inputs, selects, segmented controls) 8px.
- Panels, chart groups, popovers, menus 12px; dialogs and sheets 16px.
- Table rows, badges, and tags 4-6px.
- Full pill only for filter chips, choice pills, and round icon buttons.

## Depth

- Flat: surfaces separate by background and hairlines; no card shadows.
- Elevation only for dialogs, menus, and popovers. Light: menus `0 8px 24px rgb(16 25 38
  / 14%)`, dialogs `0 24px 64px rgb(16 25 38 / 22%)`. Dark: raise to `--surface-subtle`
  with a `--line-strong` border and `0 24px 64px rgb(4 8 14 / 55%)`. Never plain black,
  never blur behind overlays.

## Motion

- Easing `cubic-bezier(.22,1,.36,1)`; `cubic-bezier(.16,1,.3,1)` is an accepted
  alternative.
- Feedback (hover, press, toggle) 120-200ms; panels and drawers 220-320ms; layout and
  chart transitions up to 420ms, never above 650ms.
- KPI values may count from the previous value to the new one in about 380ms after a
  filter change, never on first paint; the final value is what assistive technology
  reads.
- Loading indicators loop only while loading lasts. No ambient loops, staggered entrance
  cascades, or auto-rotation.
- `prefers-reduced-motion: reduce` removes non-essential motion entirely (not merely
  shortens it); nothing essential depends on motion or hover.

Provenance: easing from the portfolio; durations from the office data dashboard tokens
(120/220/420ms) and the cash-flow-tracker counter hook (`hooks/useAnimatedCounter.ts`).

## Components

- **Filters**: one toolbar row of selects and segmented controls; active filters shown as
  removable chips with a "Reset" text button; filter state lives in the URL.
- **KPI value**: sentence-case label (0.8125rem, `--ink-secondary`), value 600 tabular,
  then a comparison line with sign, arrow icon, and words ("+4.2% vs last year").
  Colour the comparison only when a defined rule judges it good or bad, and always with
  the icon and words. Never a different accent per KPI.
- **Tables**: sticky header with uppercase or sentence-case labels and a `--line-strong`
  rule; hairline row dividers, no zebra; hover `--surface-subtle`; selected row
  `--accent-soft` with a 3px inset accent bar; horizontal scroll inside the table only.
- **Charts**: horizontal grid lines only, `--line`, dashed 3 3; axis ticks 0.75rem
  `--muted`; thin marks (2px lines, bars with 2px gaps); inverted tooltip with no shadow;
  hover crosshair or per-mark tooltip, with the same values reachable by keyboard or in
  the table view.
- **Summary or AI brief** (the conclusion block): plain text at 60-78ch under a short
  heading, followed by its basis ("From enrollment data, updated 08:15") and the actions
  it implies. A small custom line mark that fits the domain, never a sparkle, gradient,
  glow, or tinted card. States: generating (static text "Preparing summary"), failed
  (reason and Retry), stale (timestamp and Refresh).
- **States**: loading uses skeletons shaped like the final layout (no layout shift; static
  under reduced motion); empty says why and what to do next; partial shows what loaded and
  names what is missing; error keeps the last good data with its "as of" time plus Retry;
  unavailable (source down, no permission) explains without alarm colour. Unknown is
  "Not available" or an em dash with that accessible name, never 0.

## Mobile composition (720px and below)

- Top bar with title, period, and a "Filters" button showing the active count; filters
  open in a full-height sheet (native dialog).
- Navigation in a solid bottom bar (56px plus safe area, 3-5 destinations, labels at
  least 0.75rem, `--surface` with a top hairline), or a menu when there are more
  destinations.
- Same order: conclusion, actions, KPIs in two columns, then charts at full width with
  fewer ticks and direct labels.
- Tables become stacked rows: name and status left, the key figure right; drill-down
  opens a full-screen route with Back that restores scroll position.
- Verify at 320 and 390 CSS pixels.

## Do / don't

- Do lead with the conclusion and the actions it implies; keep much data, modern but
  simple.
- Do label the data source and its freshness; show uncertainty in plain language.
- Don't build a "terminal dashboard": uniform type size, no hierarchy, wall of tables.
- Don't build a "dark AI dashboard": dark default, glowing panels, neon accents.
- Don't use glow, glassmorphism, decorative gradients, or blurred backdrops.
- Don't use uppercase 9-10px micro-labels, 800-900 weights, or justified text.
- Don't use sparkle or AI-brand icons, celebratory or decorative cards, stacks of bordered
  cards, or a colour per KPI.
- Don't show implementation-maturity labels ("prototype", "seed data") to users.

## Contrast (measured, WCAG 2.2 relative luminance)

| Pair | Light | Dark | Minimum |
| --- | --- | --- | --- |
| Ink on canvas / surface / subtle / accent soft | 14.56 / 15.77 / 15.07 / 14.09 | 15.69 / 14.04 / 12.69 / 9.77 | 4.5 |
| Secondary ink on canvas / surface / subtle | 7.00 / 7.58 / 7.24 | 11.08 / 9.91 / 8.96 | 4.5 |
| Muted on canvas / surface / subtle / accent soft | 5.03 / 5.45 / 5.21 / 4.87 | 8.49 / 7.59 / 6.86 / 5.29 | 4.5 |
| Accent text on canvas / surface / accent soft | 4.86 / 5.27 / 4.71 | 8.02 / 7.18 / 5.00 | 4.5 |
| Text on accent / on accent hover / on critical | 5.27 / 7.02 / 6.57 | 7.94 / 8.83 / 6.93 | 4.5 |
| Success on surface / canvas / container | 5.19 / 4.80 / 4.78 | 7.98 / 8.92 / 6.08 | 4.5 |
| Attention on surface / canvas / container | 5.43 / 5.01 / 5.09 | 7.86 / 8.79 / 6.40 | 4.5 |
| Critical on surface / canvas / container | 6.57 / 6.07 / 5.94 | 6.27 / 7.00 / 5.31 | 4.5 |
| Tooltip ink on tooltip | 15.77 | 14.01 | 4.5 |
| Focus ring on canvas / surface / subtle / accent soft | 4.86 / 5.27 / 5.03 / 4.71 | 8.02 / 7.18 / 6.49 / 5.00 | 3 |
| Control border on surface / canvas / subtle | 3.61 / 3.33 / 3.45 | 4.30 / 4.81 / 3.89 | 3 |
| Comparison series on surface | 3.61 | 4.30 | 3 |
| Categorical 1-8 on surface (lowest) | 3.20 (slot 2) | 3.20 (slot 6) | 3 |

Line and line strong are decorative separators and carry no required information; any
boundary needed to identify a control uses `--control-border`. Measured from token values
by computation, not on a rendered page; a project still verifies the rendered result.
