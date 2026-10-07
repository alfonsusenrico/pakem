# Design Base: owner-studio

Status: Active
Applies to: marketing and portfolio surfaces
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner portfolio "Open studio" direction, approved 2026-10-02

Warm, earthy, low-saturation palette; spacious asymmetric layout; layered real product
imagery; readable sans-serif type.

## Tokens (light)
| Role | Value |
| --- | --- |
| Background | `#fffaf5` (cream) |
| Ink | `#243b49` (navy) |
| Muted text | `#4c6068` |
| Accent (interactive) | `#28594e` (forest) |
| Dark band | `#193f37` (forest) with cream text |
| Warm band | `#f5e0ce` (peach) |
| Selected tint | `#dbe6d3` (sage) |
| Decorative accent | `#d87747` (terracotta; decoration only, never text) |
| Hairline | `#d7d7c9` |

Dark theme: derive from the same roles (deep forest/navy surfaces, cream text, sage
accent) and verify contrast; the portfolio itself is light-only today.

## Type
- Display: Outfit 600, `clamp()` sizes, tracking -0.025em to -0.065em by size.
- Body: DM Sans 400, line height 1.6, paragraphs max 65ch.
- Optional third voice for short notes: Georgia italic.

## Layout and depth
- Container `min(1160px, 100% - 96px)`; section rhythm ~88px desktop, ~60px tablet.
- Asymmetric grids (for example `1fr 1.1fr`), full-bleed colour bands, subgrid for
  aligned card rows.
- Shadows soft and tinted (`0 12px 30px #193f3714`); hover "ledge" `0 6px 0 #dce5dc`.
- Buttons 7px radius, min height 46px; containers 10-16px.

## Motion
- Easing `cubic-bezier(.22,1,.36,1)`; interaction .2-.35s; handoffs 650ms.
- Ambient loops paused offscreen; 5-second auto-rotation with manual reset; hidden
  keyboard-reachable pause control; full reduced-motion support.

## Do / don't
- Do use real, sanitized screenshots with provenance; visitor-directed copy ("you").
- Don't use fake browser chrome, overlay gradients on screenshots, animated dashed
  connectors, detached ornaments, or border-colour hover changes.
