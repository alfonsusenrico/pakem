- Kernel section 6: a framework proposal (`pakem propose`) opens its pull request before
  acceptance, carrying the change itself; the owner's merge accepts it (owner decision,
  2026-10-23). Section 8: `pakem` is mandatory, never by hand, only where an area is
  stable, as ADR 0011 decision 4 says.
- Release policy (owner decision, 2026-10-23): the `pakem` tool releases whenever an
  area is proven; kernel and skill text never triggers a release alone and waits for the
  next one, batched at most once a month. ADR 0007, ADR 0011 decision 9, `AGENTS.md`, and
  `instruction-maintenance` now say the same.
