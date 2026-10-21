---
name: backend-engineering
description: Use when designing or implementing backend services - APIs and error formats, request validation, data access, database migrations, background jobs, configuration and environment variables, logging, authentication, or money, booking, and inventory logic.
---

# Backend Engineering

Defaults for the owner's backends. Check the project `AGENTS.md` for its stack,
capabilities, and any `## Overrides` first.

## 1. Stack and structure

- Default stack: Python + FastAPI + uv (especially AI services). Use NestJS when the
  client or team works in TypeScript; Next.js route handlers only for small
  full-stack apps; a headless CMS when non-developers must edit content cheaply.
- Layering: thin routes or controllers (parse, authorize, call a service, shape the
  response) -> services (domain logic; pure helpers with unit tests) -> data layer.
  No SQL or business rules in route handlers.
- Split files over about 800 lines by responsibility. Group modules by domain, not
  by technical type alone.

## 2. Always (every backend)

- **Validation**: a schema library at every trust boundary - requests, webhooks,
  queue payloads, external API responses (Pydantic; class-validator with
  `whitelist` + `forbidNonWhitelisted`; zod). Reject unknown fields on inputs the
  project defines (its API requests, its queue payloads). For third-party responses
  and webhooks, validate the fields you use and ignore the rest: providers add
  fields as a backward-compatible change.
- **Configuration**: one typed settings object, validated at startup; missing
  required values stop the app. Follow [configuration and environment
  variables](references/config-and-env.md).
- **Between release units**: code inside one release unit (a monolith, or modules
  released together) shares freely. A unit released on its own (separate repository,
  deployment, or version) depends on another only through its published, versioned
  contract: its API, or a published package version. Never through its source tree or
  repository, so one unit's releases or visibility never break another's build. Whether a
  consumer uses a published SDK or its own thin client is the project's choice, recorded
  in its `AGENTS.md`.
- **API shape**: follow [API conventions](references/api-conventions.md): plain
  resources, RFC 9457 problem+json errors with a stable `code` and `request_id`.
- **Errors**: domain code raises domain errors; one boundary layer maps them to HTTP.
  No framework HTTP exceptions inside domain services.
- **Reliability**: follow [reliability](references/reliability.md) for timeouts,
  retries, idempotency, transactions, and payload budgets.

## 3. Data (`database` capability)

Follow [data access and migrations](references/data-and-migrations.md):

- SQL-first thin data layer: parameterized SQL in per-domain query modules,
  explicit transactions and locks. An ORM only for mapping when the framework
  already brings one.
- Hand-written, idempotent, forward-only migrations that cannot change silently once
  applied; never autogenerate; never runtime schema sync. Migrations run as a separate
  step that must succeed before new app instances start.
- Tests run against a real disposable PostgreSQL with migrations applied.

## 4. Capability-triggered standards

| Capability | Apply |
| --- | --- |
| `deployed` | request id on every request (header and error body); JSON logs in production with ids, counts, durations, states only; slow-query logging; health endpoint that reports the running revision |
| `money` | [integrity kit](references/integrity-kit.md) |
| `background-jobs` | [jobs and outbox](references/jobs-and-outbox.md) |
| `api-consumers` | OpenAPI generated from code is the contract; clients generated from it; CI fails on drift; every status code documented |
| `multi-user` | authorization checked in services, not only in routes; audit trail for privileged actions; [authentication](references/authentication.md) |
| `personal-data` | `sensitive-operations` logging and privacy rules; access control and retention defined |

If your change introduces one of these capabilities, say so before implementing
and include its standards or record the gap as debt.

## 5. Security essentials

- Authentication follows [authentication](references/authentication.md).
- Compare secrets and signatures in constant time.
- Verify webhook signatures and source before processing; treat callbacks as
  untrusted input; make them idempotent.
- Set security headers (CSP, frame denial, nosniff, referrer policy) on any
  service that serves HTML.
- Rate-limit authentication, enumeration-prone, and expensive endpoints.
- Never log request bodies, tokens, personal data, or model content.
