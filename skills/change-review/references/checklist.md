# Review Checklist

Status: Active
Applies to: every diff before it is called ready
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline (2026-09); owner Working Standard Profile

## Correctness
- Matches the approved plan and acceptance; no unrequested behaviour.
- Edge cases: empty, null, duplicate, concurrent, timeout, retry, partial failure.
- Errors handled explicitly; no swallowed exceptions or invented fallback values.
- Public contracts and persisted data preserved, or migration and rollback defined.

## Security and data
- No secrets, tokens, personal data, or model content in code, logs, tests, or fixtures.
- Input validated at boundaries; authorization enforced in trusted code.
- SQL parameterized; transactions short; no network I/O inside lock-holding transactions.
- Money paths: idempotency, snapshots, locks, audit events, webhook verification.

## Tests and evidence
- Regression test for each fixed defect (fails before, passes after).
- Database code tested against real Postgres; database tests did not skip.
- UI changes: widths, themes, keyboard, 200% text, contrast evidence.
- LLM changes: evals re-run and compared.

## Maintainability
- Names reflect domain meaning; no `new`, `final`, `v2` naming.
- No dead code, commented-out code, narrating comments, or speculative TODOs.
- Files and components within size limits (about 800 / 400 lines).
- No duplicated logic that already exists in the codebase.
- Formatter-clean; no unrelated formatting churn.

## Delivery
- Conventional subject-only commits; topic branch; no AI attribution.
- `AGENTS.md` capabilities, docs, and runbooks updated when behaviour or operations changed.
- `PROJECT_STATE.md` reflects the new state.
