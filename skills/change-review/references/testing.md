# Testing Standard

Status: Active
Applies to: every project
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 9

- Every test protects a real behaviour; no placeholder tests to satisfy a checklist.
- Pure domain logic: fast unit tests.
- Code touching the database: tests against a real disposable PostgreSQL with
  migrations applied; CI fails if those tests skip.
- Mocks only for pure logic boundaries and external services (payment providers,
  model providers, email), with contract tests for their response shapes.
- Critical paths (`money`, authentication, data integrity) have integration tests
  through the real stack, including duplicate and concurrent cases.
- End-to-end and verification scripts (HTTP flows, browser checks, load or
  concurrency checks, contract diffing) are versioned in `tests/e2e/` or
  `scripts/verify/` with documented commands; their outputs go to
  `research/evidence/`; nothing durable lives in `/tmp`.
- Fixtures are synthetic, additive, and removable; never mutate shared or production
  data.
- Test isolation: reset process-level caches and environment between tests; tests
  never depend on the developer's environment variables.
- Gate on exit status, not on output: never chain a check through a pipe (`| tail`,
  `| grep`) and then `&&` into a commit or push, because the pipe's status hides the
  check's failure. Run the check, capture its status, and stop on non-zero (or use
  `set -o pipefail`).
