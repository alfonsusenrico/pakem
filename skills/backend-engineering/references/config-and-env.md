# Configuration and Environment Variables

Status: Active
Applies to: every service, worker, and script
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner backends (typed settings with fail-fast validation, 2026); Twelve-Factor App config principle

- One typed settings object per process, loaded once at startup and validated:
  missing required values or invalid values stop the process with the variable
  NAME (never the value) in the error.
- Names are UPPER_SNAKE_CASE with a component prefix and a unit suffix where a unit
  applies: `DB_POOL_MAX_SIZE`, `LLM_TIMEOUT_SECONDS`, `UPLOAD_MAX_BYTES`,
  `PAYMENT_CALLBACK_SECRET`. Booleans accept `true`/`false` only.
- `.env.example` lists every variable with a comment (purpose, required or optional,
  default) and no real values. It is the contract checked by preflight scripts.
- Secrets arrive only through the runtime mechanism (environment, secret store,
  mounted files). Never commit them, never put them in compose files, never print them.
- Defaults must be safe for production (for example cookies `Secure` by default);
  development conveniences are opt-in through explicit variables.
- Production assertions are explicit in code or preflight (for example debug mode
  off, secure cookies on, allowed origins without localhost).
- Do not read configuration ad hoc deep in the code; pass the settings object or the
  specific values.
