# Logging and Privacy

Status: Active
Applies to: all services, workers, scripts, and telemetry
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline (2026-09); incidents in owner projects (names logged, draft LLM text leaked into logs)

## Rules

- Log identifiers, counts, durations, states, and error categories. Never log
  names, contact details, credentials, free-text user input, prompt content, model
  output, or retrieved documents unless the project defines that retention, its
  access control, and its deletion.
- Log row or entity identifiers instead of personal values.
- Error messages returned to clients carry a stable code and a correlation id, not
  internal details or provider error text.
- Exception handlers must not interpolate request bodies or model output into log
  messages. Sanitize control characters before logging any external string.
- Telemetry and alerting are content-free by default and must never block or fail
  the user's request (asynchronous, bounded, failure-tolerant).
- External telemetry (tracing, analytics, LLM observability) needs a written
  decision covering destination, redaction, retention, and who can read it before
  it is enabled.

## Regression guard

Projects handling personal data or LLM content should include a test that runs a
representative failing path and asserts that known sensitive fixture values do not
appear in captured logs.
