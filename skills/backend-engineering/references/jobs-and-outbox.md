# Jobs and Outbox

Status: Active
Applies to: projects with the `background-jobs` capability
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner job queue and email outbox implementations (2026)

- Durable work goes to a jobs or outbox table written in the same transaction as the
  business change that causes it (transactional outbox).
- A separate worker process claims work with `FOR UPDATE SKIP LOCKED`, processes it,
  and records the outcome; the web process never does slow durable work inline.
- Handlers are idempotent; each job has a dedupe key, attempt count, next-run time,
  last error category (no content), and expiry.
- Retries use capped backoff; exhausted jobs move to a failed state that an operator
  can inspect and retry.
- In-process schedulers only enqueue jobs; they do not perform the work.
- Workers handle SIGTERM by finishing or releasing the current job, and support a
  run-once mode for operations and tests.
- Time zones are explicit for schedules (store UTC; schedule in the business time zone).
