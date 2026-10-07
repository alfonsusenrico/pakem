# Owner Design Base

Status: Active
Applies to: every user interface unless the project's approved design system says otherwise
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 8 (portfolio primary; office dashboard and workbench UIs secondary); typography research: Luckiesh & Moss 1942 (reviewed in Vision Research 2019), ACM CHI 2023 "How bold can we be?", Journal of Vision 2024 "Light or Bold?", IBM Carbon type sets, Fluent 2, Material 3

## Shape
- Controls (buttons, inputs, selects): radius 7-8px.
- Grouped containers (panels, dialogs, popovers): 10-16px.
- Full pill only for chips, choice pills, and round icon buttons.
- Table rows, tags, and badges: 4-6px.
- Rectangular, sharp controls read as stiff; avoid them.

## Colour
- Exactly one interactive accent per product, chosen per project.
- Semantic colours (success, attention, critical) only for real state, always paired
  with text or an icon; never colour alone.
- Neutrals carry the interface; a decorative secondary colour is allowed only where it
  carries no meaning and is never needed for contrast.

## Structure
- Hierarchy through alignment, spacing, type, and 1px hairlines.
- Cards only for selectable or interactive items; no stacks of boxed cards, no
  decorative or celebratory cards, no nested cards.

## Typography
- Headings and KPI values weight 600 (500-650 depending on the typeface); body 400;
  text 12px and smaller may use 500-600. Never 800 or heavier for UI text; nothing
  lighter than 400.
- Tabular figures for numbers in tables, KPIs, and anything compared in columns.
- Uppercase only for table column headers and field labels, at least 11px and 4.5:1
  contrast; never as eyebrows on public pages.
- Prose left-aligned (never justified), 60-78 characters per line, line height ~1.6.
- Display headings may use slight negative tracking; UI text uses 0.
- Relative units that respect user settings; fluid `clamp()` for display sizes.

## Fonts
- Marketing sites: one self-hosted expressive pair.
- Tools: system stack, or one self-hosted family or pair when it strengthens the
  product identity (at most 2 families, only the weights actually used).
- Verify the rendered face and weights; no synthetic bold or italic.

## Icons
- Line icons, stroke ~1.6-1.8, round caps and joins, sized with the text; custom or an
  outlined set. Brand logos filled.

## Themes
- Every surface ships light and dark themes; light is the default; the choice is
  persisted and follows the operating-system preference until the user changes it.
- Check contrast in both themes.

## AI surfaces
- No sparkle or AI-brand icons, no glow, no decorative AI art.
- User messages may be soft bubbles; assistant answers are plain text blocks.
- Each product gets its own assistant mark that fits its domain.

## Anti-patterns
- Glow, neon, glassmorphism, decorative gradients, rainbow palettes.
- Different accent colours for every KPI; excessive coloured pills.
- Controls that do nothing; hover-only information; scroll hijacking; cursor gimmicks.
- Fake browser chrome, fabricated captions, or simulated data shown as real.
