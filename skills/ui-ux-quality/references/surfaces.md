# Surfaces

Status: Active
Applies to: choosing depth, motion, density, and layout per surface
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 8.3

A project declares its surface(s) in `AGENTS.md` (`Design surface: marketing`,
`tool`, or both).

| | Marketing / portfolio | Tools: dashboards and workbenches |
| --- | --- | --- |
| Density | airy; generous section rhythm | dense but aligned; 4px spacing base with 16/24/32 gaps |
| Layout | asymmetric grids, full-bleed bands, layered media | sidebar + toolbar, or rail + master-detail; one stable action area |
| Depth | soft shadows allowed, tinted with ink or accent, never plain black | flat; elevation only for dialogs, menus, popovers |
| Motion | ambient and auto-rotating motion allowed only with all safeguards (below) | functional only: feedback 120-350ms, transitions up to ~650ms, no ambient loops |
| Information order | story: problem, proof, invitation | conclusion first, then actions, then supporting numbers, then evidence and drill-down |

## Motion safeguards (marketing)
- Pause animation when offscreen or the tab is hidden.
- Auto-rotation (for example every 5 seconds) restarts its full interval after any
  manual choice; never rotates content the user is reading.
- A global pause control reachable by keyboard.
- `prefers-reduced-motion` removes non-essential motion entirely.
- Pointer effects only for fine pointers.

## Easing and duration
- Interaction feedback: 120-350ms, ease-out.
- Larger handoffs and layout changes: 400-650ms with a strong ease-out
  (`cubic-bezier(.22,1,.36,1)` or `cubic-bezier(.16,1,.3,1)`).
- Nothing essential depends on motion or hover.
