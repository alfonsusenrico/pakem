# Frontend Engineering

Status: Active
Applies to: web frontends
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 7

- **Tokens first**: colours, type, spacing, radii, shadows, motion, and z-index are
  CSS custom properties in one tokens file per project; components use tokens, never
  raw values. Hand-written CSS, Tailwind bound to the tokens, or a component library
  is a project choice recorded in `AGENTS.md`.
- **One source of truth for design**: the tokens file plus the project's design notes.
  `AGENTS.md` links to them; it does not restate colour values.
- **Components**: shared primitives in `components/ui/` (button, field, dialog,
  drawer, tabs, table, badge, empty and error states); feature folders for the rest.
  Split components over about 400 lines; extract hooks for state and data logic.
- **Phone composition**: phone layouts are designed for the phone (navigation,
  density, disclosure, primary action), never just stacked desktop columns. Verify at
  320 and 390 CSS pixels and up.
- **States**: every data view handles loading, empty, partial, error, and
  unavailable states truthfully.
- **Formatting and checks**: Prettier with default options, ESLint, TypeScript strict,
  all in CI.
- **Performance**: self-hosted fonts with `display: swap` and only used weights;
  responsive modern image formats with explicit dimensions; lazy-load below-the-fold
  media; no layout shift from late content.
- **Data and forms libraries** are project choices; whatever is chosen is used
  consistently (no ad hoc fetches in components when a data layer exists).
