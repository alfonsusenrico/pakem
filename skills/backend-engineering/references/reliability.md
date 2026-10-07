# Reliability

Status: Active
Applies to: services, workers, and integrations
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline (2026-09); owner incidents (deadlock from writes inside a locking transaction; request-size limits at a gateway)

- Every outbound call has a timeout chosen from the dependency's real behaviour;
  no unbounded waits.
- Retry only idempotent or idempotency-keyed operations, with capped exponential
  backoff and jitter; honour `Retry-After`. Never retry a request that may have been
  applied unless it carries an idempotency key.
- Distinguish "not sent", "sent but unknown", and "failed" outcomes for external
  calls, and record which one happened.
- Bound payload sizes on both sides; check sizes before sending to a gateway with a
  limit, with margin.
- Batch large datasets; never assume a full dataset fits one request or one
  transaction.
- Keep transactions short; no network I/O or telemetry inside them.
- Define behaviour for duplicate delivery, cancellation, restart, and partial failure
  of multi-step operations.
- Background telemetry and alerting are asynchronous, bounded, and never fail the
  user's request.
