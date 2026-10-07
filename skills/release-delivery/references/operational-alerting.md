# Operational Alerting

Status: Active
Applies to: deployed services and workers
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner alerting library and incident reporting (2026)

- Alerts are asynchronous, bounded, deduplicated, and fail-open: an alerting failure
  never fails the user's request or the job.
- Content-free: event type, component, environment, revision, request or job id,
  error category, counts. Never payloads, personal data, secrets, or model content.
- Alert on terminal failures and sustained degradation, not on every retry.
- Each alert type has an owner action documented in the runbook.
- Deployment notifications report environment, revision, result, and job link.
