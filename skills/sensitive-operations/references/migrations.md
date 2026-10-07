# Migration Safety

Status: Active
Applies to: database schema and data migrations, persisted-format changes
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline (2026-09); migration-identifier length incident in an owner project

## Rules

- Use the project's versioned migration tool; never hand-edit schema in a shared
  environment.
- Migration identifiers and generated names must fit the storage that records them
  (for example a version-table column length). Add a test when the tool does not
  check this itself.
- Prefer expand, migrate, contract: add new structures compatible with the old
  code, deploy code that writes both or reads either, backfill, then remove old
  structures in a later release.
- Define ordering against application deploys, the compatibility window, recovery,
  and rollback before running anything outside a local environment.
- Account for mixed-version operation, retries, concurrency, restarts, and partial
  failure. Backfills must be idempotent and resumable in bounded batches.
- Do not hold locks while calling the network, emitting telemetry, or waiting on
  external services.
- Prove data preservation (row counts, checksums, or targeted queries) and the
  relevant negative paths before calling a migration complete.
- Destructive steps (drop, truncate, irreversible transforms) need an explicit
  backup or recovery path and separate approval.
