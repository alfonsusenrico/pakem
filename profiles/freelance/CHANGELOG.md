# Changelog

All notable changes to the `freelance` profile. Versions follow semantic versioning;
`VERSION` and `profile.toml` change together.

## [1.1.0] - 2026-10-27

### Changed

- `freelance-delivery` writes every document with `pakem docs` (framework ADR 0014):
  simpler stages (fixed scope `discovery` to `closed`, workstream `active` to
  `closed`), numbering and records kept by `pakem docs`, fixed wording from the
  store's approved `wording/` files, and the full document catalogue in layout D.

### Removed

- The proposal generator section and its contract references.

## [1.0.0] - 2026-10-27

### Added

- Profile split from `profiles/personal` (ADR 0001, framework ADR 0012), with its
  `freelance-delivery` and `client-engagement` skills.
- Digest for client repositories: the engagement record first, and a stop until the
  AI-provider answer is recorded; per-engagement spending with no free-first preference;
  identity; local-only process files and no trace of agent use; client conventions and
  language; client systems, releases, and server setup only when assigned; no invented
  commercial facts.
- `client-engagement` records who reviews and merges, the release and server work
  assigned, whether client code and data may reach AI providers, and whose account pays.
