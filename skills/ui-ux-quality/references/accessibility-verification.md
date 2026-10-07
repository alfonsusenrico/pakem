# Accessibility and Evidence

Status: Active
Applies to: every user interface
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: W3C WCAG 2.2 and its Understanding documents; owner baseline UI guide (2026-09)

## Target
- WCAG 2.2 Level AA unless a governing policy sets another level. Use W3C success
  criteria as the authority, not a scanner or skill.

## Semantics and interaction
- Native elements first (`button`, `a`, `dialog`, `details`, form controls).
- Accessible names and labels; keyboard operation for everything; Escape closes
  overlays and focus returns to the trigger.
- Visible, unobscured focus (2-3px outline with offset).
- Targets around 44x44 CSS pixels; never below the 24x24 minimum without the spacing
  exception.
- Respect reduced motion. No information conveyed by colour alone.
- Do not block password managers or paste; no cognitive-test authentication.

## Contrast (measured on the rendered page)
- Normal text at least 4.5:1, large text at least 3:1.
- Focus indicators and required non-text indicators at least 3:1.
- Check both themes.

## Responsive and overrides
- 200% text resize, 400% zoom (320px reflow), WCAG text-spacing overrides: nothing
  clipped, hidden, or overlapped; no two-dimensional scrolling except for content
  that needs it (data tables).
- Widths: 320, 390, 768, 1024, 1440, 1920 as relevant to the surface.

## Evidence
- Record what was checked and how in the change's `verification.md`: widths, themes,
  200% text, text spacing, keyboard pass, automated axe results, measured contrast.
- Keep reusable browser checks in `tests/e2e/` or `scripts/verify/`; outputs go to
  `research/evidence/`.
- Distinguish code inspection, automated results, browser checks, and
  assistive-technology checks. Say "not a conformance audit" unless one was done.
