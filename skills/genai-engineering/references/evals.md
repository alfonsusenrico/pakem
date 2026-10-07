# Evals

Status: Active
Applies to: every LLM feature
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: Anthropic, "Demystifying evals for AI agents" (2026); owner model benchmarks with development and held-out sets (2026)

- **Datasets**: a development set for iteration and a held-out set never used while
  tuning. Cover normal, incomplete, ambiguous, historical, adversarial, and
  out-of-scope cases. Synthetic or anonymized data only; record provenance.
- **Graders**: deterministic checks first (schema, exact fields, ids, refusals); model
  graders only with a written rubric and calibration against human labels; human
  review for subjective quality.
- **Metrics**: task success per case; for agents also tool-call correctness and steps.
  Use repeated trials to measure reliability (pass@k for "can it", pass^k for "does it
  every time") when outputs vary.
- **Capability vs regression**: capability evals measure what the feature can do;
  regression evals guard what already works and run on every change.
- **Gate**: a model, prompt, provider, or parameter change ships only with a recorded
  comparison against the current baseline (quality, cost, latency) in the change's
  `verification.md`.
- Evals live in the repository (for example `evals/`) with a documented command;
  results go to `research/evidence/`.
