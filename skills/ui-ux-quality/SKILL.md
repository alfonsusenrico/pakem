---
name: ui-ux-quality
description: Use when creating, materially changing, or reviewing a user interface or interaction - layout, visual design, typography, colour, motion, components, frontend structure, accessibility, or choosing a design direction. Applies the owner's design base and verifies accessibility with evidence.
---

# UI and UX Quality

## 1. Authority

- The project's approved users, content, design system, tokens, and components come
  first. Read the project `AGENTS.md` for its declared surface and design base, and
  its tokens file, before changing anything visual.
- Where the project is silent, apply the owner's [design base](references/design-base.md)
  and the rules for its [surface](references/surfaces.md). Projects that adopt a named
  [design base](references/design-bases/README.md) use its tokens; the marketing base is
  [owner-studio](references/design-bases/owner-studio.md), the tool bases are
  [owner-dashboard](references/design-bases/owner-dashboard.md) and
  [owner-workbench](references/design-bases/owner-workbench.md).
- After a direction is accepted: refine, don't restyle. Never replace established
  tokens or identity to make something look different.
- Where taste is uncertain, prefer research-backed defaults and cite the source.

## 2. Choosing or changing a direction

For a new product direction or a major redesign, follow
[design direction](references/design-direction.md): build two to four working
alternatives, compare them in the browser with the owner, record the choice and the
rejected options. Do not pick a direction from a written brief alone.

## 3. Building

Follow [frontend engineering](references/frontend-engineering.md): tokens first,
shared primitives, size limits, purpose-built phone composition, formatter.

## 4. Content

- Truthful content only: no invented metrics, outcomes, testimonials, or placeholder
  labels presented as real. Unknown is never shown as 0. Progress comes only from
  real counts.
- No captions or explanations that do not lead to an action or a decision.
- Copy in the audience's language; engineering identifiers stay in English.

## 5. Accessibility and verification

Target WCAG 2.2 AA and verify with [accessibility and
evidence](references/accessibility-verification.md). Never claim conformance
without evidence that covers the pages, states, widths, and flows involved.

## 6. Review output

Report findings with file and line references, separated into code inspection,
automated checks, browser checks, and assistive-technology checks. Do not invent
metrics or evidence.
