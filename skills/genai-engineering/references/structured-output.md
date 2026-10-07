# Structured Output

Status: Active
Applies to: model output consumed by code
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner implementations (strict JSON schema output with validation, 2026)

- Use the provider's strict schema mode when available; otherwise request JSON and
  validate.
- Validate with the same schema library as the rest of the boundary (Pydantic, zod).
- Repair policy: at most one or two bounded retries with the validation error, then
  fail closed with an explicit outcome (for example `unparsed_output`), never invented
  values.
- Acceptance is structural: code checks schema, required fields, enums, ranges, ids
  that must match supplied data. Code does not judge wording quality; that belongs to
  evals.
- Facts in the output (numbers, ids, names) must come from supplied data; verify them
  against the source before storing.
- Store the model, prompt version, and validation outcome with each stored result.
