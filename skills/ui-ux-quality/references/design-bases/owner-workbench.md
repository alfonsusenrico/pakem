# Design Base: owner-workbench

Status: Draft (do not adopt until the owner approves it)
Applies to: task workbenches (master-detail review, ledgers, editors, assistant panels)
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner portfolio (primary; rules and discipline), office task workbench web UI (approved report-workbench treatment), cash-flow-tracker web UI (ledger workbench, drawers), office data dashboard web UI (semantic model); W3C WCAG 2.2

A focused desk for working through records: a navy rail on the left, a white
master-detail workspace on a cool canvas, and one stable place where actions live.
Selection is shown by a soft accent tint with a slim accent bar, status by quiet tonal
badges with words, and density by tight alignment rather than boxes. The assistant is a
tool beside the work, not a spectacle: questions in soft bubbles, answers as plain text,
a custom mark, no sparkle. Both themes are calm navy; flat everywhere except overlays.

## Tokens

Neutral, semantic, and focus tokens are identical to `owner-dashboard` so one product can
host both surfaces; the rail tokens are added. Values marked "(default; project may
change)" are recommendations from the evidence; re-measure contrast after any change.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Canvas | `--canvas` | `#f4f6f9` | `#101926` |
| Surface | `--surface` | `#ffffff` | `#182334` |
| Subtle surface (hover, read-only fields, raised overlay in dark) | `--surface-subtle` | `#f8fafc` | `#1e2b3e` |
| Ink | `--ink` | `#172337` | `#edf2f8` |
| Secondary ink (labels, column headers) | `--ink-secondary` | `#475569` | `#c3cedb` |
| Muted (meta, placeholders, hints) | `--muted` | `#5b6b7f` | `#a8b5c6` |
| Line (hairline dividers) | `--line` | `#dfe6ee` | `#2c3b4f` |
| Line strong (pane edges, header rules) | `--line-strong` | `#cad5e2` | `#3b4c62` |
| Control border (inputs, textareas, composer; >=3:1) | `--control-border` | `#7a889b` | `#75879e` |
| Accent: interactive only, chosen per project (default; project may change) | `--accent` | `#1769d2` | `#78b3f3` |
| Accent hover | `--accent-hover` | `#1156b1` | `#8abcf6` |
| Accent soft tint (selected row, user bubble, mark) | `--accent-soft` | `#eaf3ff` | `#193d68` |
| Text on accent | `--on-accent` | `#ffffff` | `#0b1a2e` |
| Focus ring (workspace) | `--focus-ring` | `#1769d2` | `#78b3f3` |
| Success / container | `--success` / `--success-container` | `#257b55` / `#edf8f2` | `#76c99c` / `#1d3c31` |
| Attention / container | `--attention` / `--attention-container` | `#9a5b00` / `#fff7e7` | `#e5ad57` / `#3c301c` |
| Critical / container | `--critical` / `--critical-container` | `#b42318` / `#fff0f0` | `#ee8585` / `#442729` |
| Rail (default; project may change) | `--rail` | `#102b4f` | `#0a182b` |
| Rail selected item | `--rail-selected` | `#21416a` | `#192f4a` |
| Rail ink | `--rail-ink` | `#e9f1fb` | `#e8eef7` |
| Rail muted | `--rail-muted` | `#a8bad0` | `#9eb0c7` |
| Rail indicator and rail focus ring | `--rail-indicator` | `#78b3f3` | `#78b3f3` |
| Overlay scrim | `--scrim` | `rgb(16 25 38 / 42%)` | `rgb(4 8 14 / 60%)` |

- The rail is a neutral (the ink family), not a second accent. The navy rail is the
  default; a project may use a light rail on `--surface` with a hairline instead.
- Focus inside the rail uses `--rail-indicator`; the workspace focus ring does not reach
  3:1 on the light-theme rail.
- Charts inside a workbench use the chart roles of `owner-dashboard`.

Provenance: neutrals, rail, and dark theme from the office task workbench
(`src/web-ui/src/teacher-desk.css`, `--desk-*`), with muted (`#6e7d91`, 4.20:1 on white)
and control borders darkened to pass contrast and the rail selection made a solid colour
from its 16% indicator tint; semantic tonal containers from the same file, with light
attention and critical darkened to pass on the canvas; the semantic model (main colour
plus container) from the office data dashboard (`src/web-ui/src/design-tokens.css`).
Neutral temperature is cool because both office tools agree (default; project may
change); a project may derive warm neutrals from `owner-studio` if it re-measures every
pair.

## Type

- Family: system stack `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
  Arial, sans-serif` (default). A project may self-host one family when it strengthens
  identity (at most two families, only the weights used); the portfolio's DM Sans is the
  suggested owner choice when one is wanted. Confirm it has tabular figures
  before adopting it.
- Weights: headings 600 (a variable face may use 550-650); record titles 600; body 400;
  text of 12px and below 500-600. Never 800 or heavier; nothing below 400.
- Scale (rem at a 16px root): meta and badges 0.75rem; dense cells, list rows, and form
  text 0.8125-0.875rem; assistant answers and long notes 0.9375rem; section title 1rem;
  page title 1.3125rem; record title in the detail header 1.375rem. Body line height 1.5;
  assistant answers and prose 1.6-1.65 at 60-78ch, left-aligned.
- Tabular figures for scores, amounts, counts, and dates in columns; amounts
  right-aligned.
- Tracking 0 for UI text; uppercase table and field labels +0.05em (design base rule). Uppercase only for table column
  headers and field labels: 0.75rem (never below 11px), weight 600, `--ink-secondary`
  (on the rail, `--rail-muted`).

Provenance: rules from the owner design base; sizes from the office task workbench
(record title 22px, page title 21px, answers 15px/1.65) raised where it used 9-11px labels.

## Spacing and density

- 4px base: 4, 8, 12, 16, 24, 32, 48. Gaps: 16 inside a group, 24 between groups, 32
  between page sections. Workspace padding 24px (32-40px on wide screens).
- Control height 36px with pointer: fine; 44px with pointer: coarse; targets never
  below 24x24.
- Master list rows at least 56px (two lines: name, then meta); table rows 40px; detail
  pane padding 24-28px.

## Layout

- Shell: rail 244px expanded, 72px collapsed at 1023px and below; workspace top bar
  (64-80px) with page title, status, and the assistant button; content on `--canvas`.
- Master-detail: one surface with a `--line` frame and 12px radius; list column
  `minmax(225px, 27%)` with its own search and filters at the top and independent
  scroll; detail column with header (record title, meta, status badge), underline tabs
  (42px, 2px accent bar), then content.
- One stable action area: page-level and batch actions in the workspace toolbar,
  right-aligned; record actions in a sticky bar at the bottom of the detail pane, with
  processing and save state ("Saved 10:42") in that bar. Never repeat the same call to
  action in a second place.
- Ledgers: filter row, table, and an inline detail drawer (320px, left hairline) that
  narrows the table instead of covering it on wide screens.
- Attachable side panel (assistant, notes, history): `clamp(360px, 30vw, 460px)`; at
  1101px and above it docks and narrows the workspace; below that it overlays with a
  shadow; at 720px and below it is full screen.
- Container queries (`container-type: inline-size`) inside the detail pane and panels,
  so content reflows when the side panel docks; media queries only for the shell.

Provenance: rail, master-detail ratios, tabs, docking widths, and container queries from
the office task workbench; ledger drawer from cash-flow-tracker
(`components/ui/DetailDrawer.tsx`, `components/ui/FilterBar.tsx`).

## Shape

- Controls (buttons, inputs, selects, textareas, segmented controls) 8px.
- Master-detail frame, panels, menus, popovers, suggestion buttons 12px; dialogs and the
  assistant composer 16px.
- List rows, table rows, badges, and tags 4-6px; selection uses a 3px inset accent bar.
- Full pill only for filter chips, choice pills, and round icon buttons (send, close,
  assistant mark).
- User bubble 16px with a 6px corner toward the user's side.

## Depth

- Flat: panes separate by hairlines; no card shadows.
- Elevation only for dialogs, menus, popovers, and an overlaying side panel. Light:
  menus `0 8px 24px rgb(16 25 38 / 14%)`, dialogs `0 24px 64px rgb(16 25 38 / 22%)`,
  overlay panel `-16px 0 40px rgb(16 25 38 / 10%)`; a docked panel has no shadow. Dark:
  raise to `--surface-subtle` with a `--line-strong` border and `0 24px 64px rgb(4 8 14 /
  55%)`. Never plain black; no blur behind overlays.

## Motion

- Easing `cubic-bezier(.22,1,.36,1)`; `cubic-bezier(.16,1,.3,1)` is an accepted
  alternative.
- Feedback 120-200ms; dialog rise and fade 160-200ms; panel dock and drawer 220-320ms;
  never above 650ms.
- Streaming caret and typing dots loop only while an answer is streaming and are static
  under reduced motion. No ambient loops, entrance cascades, or auto-rotation.
- `prefers-reduced-motion: reduce` removes non-essential motion entirely; nothing
  essential depends on motion or hover.

Provenance: easing from the portfolio; dialog timings (160/180ms) and streaming
indicators from the office task workbench.

## Components

- **Master list**: search field, filter checkboxes, then rows (name 600, meta in
  `--muted`, status badge or completion mark on the right). Selected row: `--accent-soft`
  with the 3px accent bar; keyboard Up/Down moves selection; the list keeps its scroll
  position when the detail changes.
- **Status badges**: semantic text on its container, 4px radius, 0.75rem weight 600, with
  a word ("Pending", "Done") and an icon where the state needs attention.
- **Editors and notes**: label above the field; `--control-border`; read-only shown on
  `--surface-subtle` with `--muted` text; inline validation with icon and text below the
  field; unsaved changes guarded on navigation.
- **Destructive actions**: inline confirmation on `--critical-container` with Cancel and a
  critical button; never a single click.
- **Assistant panel**: floating mode is a native modal `dialog`
  (`min(100%, 880px)` by `min(100%, 820px)`, 16px radius) that traps focus and returns it
  to the opener; attached mode is the side panel above. Header: title, context subtitle,
  mode toggle, clear (confirmed), close. Conversation column at most 720px: user
  questions right-aligned in `--accent-soft` bubbles (max 80%); assistant answers as
  plain text blocks beside a 28px round mark in `--accent` on `--accent-soft`, drawn as a
  custom line glyph that fits the domain (stroke 1.6-1.8, round caps). Welcome state with
  a short purpose line and 2-4 suggestion buttons. Composer: one 16px container with
  `--control-border`, focus shown on the container (`:focus-within`), auto-growing text
  area, round send button in `--accent`.
- **States**: loading skeletons shaped like the final pane (static under reduced motion);
  empty list says why and offers the next step; partial and failed processing name the
  affected records and offer Retry; unavailable source explains without alarm colour.
  Unknown is "Not available", never 0; progress comes only from real counts.

## Mobile composition (720px and below)

- The rail becomes a top bar with a menu button that opens navigation in a dialog.
- Master-detail becomes two views: the list first; choosing a record opens its detail
  with Back, restoring list scroll and selection.
- The action area becomes a sticky bottom bar (plus safe area) with the primary action
  at full width and secondary actions in a menu.
- Detail tabs scroll horizontally; tables become stacked rows with the key figure on the
  right.
- Side panels and the assistant open full screen; the mode toggle is hidden.
- Verify at 320 and 390 CSS pixels.

## Do / don't

- Do keep much data visible, modern but simple; refine the accepted desk, don't restyle
  it.
- Do keep one place for actions and show progress and save state there.
- Don't use sparkle or AI-brand icons, glow, gradients, or decorative AI art; don't put
  assistant answers in bubbles.
- Don't use glassmorphism or blurred backdrops, including the dialog scrim.
- Don't use uppercase 9-10px micro-labels, 800-900 weights, or justified text.
- Don't stack bordered cards, nest cards, or add celebratory or decorative cards.
- Don't leave stranded context cards in a side column disconnected from the record.
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
| Rail ink on rail / rail selected | 12.46 / 9.09 | 15.28 / 11.64 | 4.5 |
| Rail muted on rail / rail selected | 7.16 / 5.22 | 8.05 / 6.13 | 4.5 |
| Focus ring on canvas / surface / subtle / accent soft | 4.86 / 5.27 / 5.03 / 4.71 | 8.02 / 7.18 / 6.49 / 5.00 | 3 |
| Rail indicator on rail / rail selected | 6.44 / 4.70 | 8.10 / 6.17 | 3 |
| Control border on surface / canvas / subtle | 3.61 / 3.33 / 3.45 | 4.30 / 4.81 / 3.89 | 3 |

Line and line strong are decorative separators and carry no required information; any
boundary needed to identify a control uses `--control-border`. Measured from token values
by computation, not on a rendered page; a project still verifies the rendered result.
