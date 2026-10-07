# AI Observability

Status: Active
Applies to: every LLM feature in deployed projects
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: OpenTelemetry GenAI semantic conventions (status Development as of 2026-08; attribute names may change)

- Emit content-free spans and metrics: operation, provider, model, prompt id and
  version, token counts, latency, outcome category, retry count, cost estimate.
- Content capture (prompts, outputs, documents) is off by default; enabling it needs a
  written decision: environments, redaction, destination, retention, and who can read
  it.
- Correlate model calls with the request id.
- Prefer OpenTelemetry GenAI attribute names but isolate them behind one module,
  because the conventions are not yet stable.
