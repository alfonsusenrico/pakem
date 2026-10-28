---
name: ui-ux-quality
description: Use when creating, materially changing, or reviewing a user interface or interaction - deciding what the interface is for, navigation, layout, data display, visual design, frontend structure, accessibility, or choosing a design direction. Works from the product's purpose and verifies accessibility with evidence.
---

# UI and UX Quality

## 1. Authority

- The project's approved users, content, design system, tokens, and components come
  first. Read the project `AGENTS.md` (its `Design surface:`), its tokens file, and its
  design notes before changing anything visual.
- Visual style - colour, type, shape, icons, motion, tone - is a project decision,
  recorded in the project's tokens file and design notes. The framework sets none.
- Where the project has no design yet, choose a direction with the owner (section 3);
  never invent a style silently. After a direction is accepted: refine, don't restyle.
- Where taste is uncertain, prefer research-backed defaults and cite the source.

## 2. Start from what the interface is for

Follow [surfaces](references/surfaces.md): decide what the product mostly is - for
example a data dashboard, a public website, a web application, or a mix - record it as
`Design surface:` in `AGENTS.md`, and apply that surface's goals, questions, and checks.
The examples there are not a closed list; derive the same parts for anything else.

## 3. Choosing or changing a direction

For a new product direction or a major redesign, follow
[design direction](references/design-direction.md): build two to four working
alternatives, compare them in the browser with the owner, record the choice and the
rejected options. Do not pick a direction from a written brief alone.

## 4. Building

Follow [frontend engineering](references/frontend-engineering.md): tokens first,
shared primitives, size limits, purpose-built phone composition, formatter.

## 5. Rules for every interface

- Truthful content only: no invented metrics, outcomes, testimonials, or placeholder
  labels presented as real. Unknown is never shown as 0. Progress comes only from real
  counts.
- No captions or explanations that do not lead to an action or a decision.
- Copy in the audience's language; engineering identifiers stay in English.
- Light and dark themes; the choice is persisted and follows the operating-system
  preference until the user changes it. Check contrast in both.
- Design each theme for its own background: choose and check a light theme on light
  surfaces, never by inverting or reusing the dark one. Check every text, icon, mark and
  divider against the surface it sits on, in every theme, including content drawn by other
  programs (an embedded terminal, a third-party widget). Brand marks keep their colours.
- Meaning is never carried by colour alone.
- Phone layouts are designed for the phone, not stacked desktop columns.

## 6. Accessibility and verification

Target WCAG 2.2 AA and verify with [accessibility and
evidence](references/accessibility-verification.md). Never claim conformance
without evidence that covers the pages, states, widths, and flows involved.

## 7. Review output

Report findings with file and line references, separated into code inspection,
automated checks, browser checks, and assistive-technology checks. Do not invent
metrics or evidence.
