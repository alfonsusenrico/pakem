# Design Bases

Status: Active
Applies to: projects that adopt a named design base
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner Working Standard Profile section 8; UI study 2026-10-07 and owner statement 2026-10-08 (portfolio primary; cash-flow-tracker web UI, then the office dashboard and workbench UIs as secondary references)

A design base is a concrete starting point (tokens and component character) for a
surface. Each base file states: surface, tokens (colour roles with values for light and
dark, type, spacing, radii, depth, motion), component character, mobile composition,
do/don't, and measured contrast. Bases refine the owner design base; they never
override it.

Available bases:
- `owner-studio` (`owner-studio.md`) - marketing and portfolio surface, from the owner's
  approved portfolio (2026-10).
- `owner-dashboard` (`owner-dashboard.md`) - tool surface: data dashboards and analytics
  consoles (draft: do not adopt until the owner approves it).
- `owner-workbench` (`owner-workbench.md`) - tool surface: task workbenches with
  master-detail review, ledgers, editors, and assistant panels (draft: do not adopt
  until the owner approves it).

`owner-dashboard` and `owner-workbench` share identical neutral, semantic, and focus
tokens, so a product with both kinds of screen adopts one of them for its tokens and
follows the other's layout and component sections where those screens appear.

## Adopting a base

1. Declare it in the project `AGENTS.md`: `Design surface: <marketing|tool>` and
   `Design base: <name>`.
2. Copy the base's tokens for both themes into the project's single tokens file as CSS
   custom properties; components use the tokens, never raw values.
3. Choose the product's one interactive accent (and its hover, soft tint, and on-accent
   text); values marked "(default; project may change)" in a base are recommendations.
4. Re-measure contrast for every changed pair in both themes (text 4.5:1, large text
   3:1, focus and required non-text indicators 3:1) and record it in the change's
   verification notes.
5. After the direction is accepted, refine; don't restyle.
