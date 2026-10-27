# Changelog

All notable changes to the `personal` profile. Versions follow semantic versioning;
`VERSION` and `profile.toml` change together.

## [1.0.1] - 2026-10-27

### Changed

- Workspace map: commercial records stay in the `pakem docs` store, which replaces the
  proposal generator.

## [1.0.0] - 2026-10-27

### Changed

- The profile covers the owner's own projects only (ADR 0002, framework ADR 0012). New
  digest: free first weighed against quality (`default:free-first`), AI calls through
  the owner's gateway, GitHub and runners, per-project hosts and environments, English,
  the personal email, the owner merges.

### Removed

- The `freelance-delivery` and `client-engagement` skills and the client-repository
  digest lines, now in `profiles/freelance`.

## [0.3.0] - 2026-10-21

### Changed

- Renamed with the framework (Pakem 2.0.0). This entry was added later.

## [0.2.0] - 2026-10-12

### Changed

- Client repositories: agents push and report the compare link; the owner opens the
  merge request (`default:mr-policy` override), because the framework default now
  has agents open merge requests.
- `freelance-delivery` records keep principles only: evidence levels, one numbering
  allocator with no reuse, corrections as new records. Field lists, statuses, and
  number formats belong to the proposal generator's contract.

### Removed

- The record field tables and the `PRJ-`/`PROP-`/`FINAL-`/`INV-` numbering scheme.

## [0.1.0] - 2026-10-09

### Added

- Profile skeleton (`profile.toml`, `VERSION`, `AGENTS.md`) and ADR 0001 (scope and
  location).
- Digest for personal and freelance repositories: identity, client-repository
  privacy check, client conventions as overrides, client systems and contact only
  on owner instruction, no invented commercial facts, handover standard, skill
  pointers.
- Skill `freelance-delivery`: engagement lifecycle from discovery to closure, with
  references for stages, records and numbering, and document structures.
- Skill `client-engagement`: working inside a client's or partner team's repository,
  with references for sources of truth, meetings and decisions, and deploy handoff
  packages.
- Personal workspace map (`workspace/AGENTS.md`).
