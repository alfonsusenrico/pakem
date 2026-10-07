# Data Access and Migrations

Status: Active
Applies to: projects with the `database` capability
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner backends (raw SQL with locks, hand-written idempotent migrations, an incident where runtime schema sync dropped foreign keys, 2026)

## Data access

- PostgreSQL by default.
- Parameterized SQL only; never string-built SQL with user input.
- Per-domain query or repository modules own SQL; services call them inside explicit
  transactions.
- Choose locking deliberately: `SELECT ... FOR UPDATE` for rows being changed;
  `FOR UPDATE SKIP LOCKED` for work claiming; `pg_advisory_xact_lock` for logical
  resources (an inventory slot, a settlement batch).
- No network calls, telemetry writes, or waiting on external services while holding
  row or advisory locks.
- Declare column types explicitly (ids, money as `numeric` or integer minor units,
  timestamps as `timestamptz`).

## Migrations

- Hand-written; never autogenerate and never let the ORM synchronize schema at runtime.
- Idempotent (`IF NOT EXISTS`, catalog checks) and forward-only; checksummed by the
  migration runner so an applied file cannot change silently.
- Migration identifiers fit the storage that records them; add a test when the tool
  does not check this.
- Run as a separate step (compose one-shot service or deploy step) before new app
  instances start; not at app startup when more than one instance can run.
- Prefer expand -> migrate -> contract across releases for breaking changes.
- CI applies migrations to an empty database, re-applies them (must be a no-op), and
  runs the tests against the result.
- Production migrations take a backup first; destructive steps need separate approval
  (`sensitive-operations`).
