# Layout Principles

Status: Active
Applies to: new repositories and restructures
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner projects (2025-2026); owner baseline (no universal directory tree)

There is no universal tree; follow the framework's idiom and these principles.

- Name directories for responsibility and domain (`orders/`, `payments/`), not for
  history (`new/`, `v2/`, `legacy2/`).
- Separate deployable units clearly (`backend/`, `frontend/`, `worker/`, or
  `src/app/` + `src/web-ui/`); each has its own manifest and commands.
- Backend: routes or controllers, services (domain), data layer (queries,
  migrations), clients for external systems, settings, prompts (when `llm`).
- Frontend: tokens file, `components/ui/` primitives, feature folders, `lib/` for pure
  helpers, `hooks/`.
- Tests next to code or in `tests/` mirroring the source tree; end-to-end and
  verification scripts in `tests/e2e/` and `scripts/verify/`.
- Operational scripts in `scripts/` with a one-line purpose header; deployment files
  in `deploy/` or `deployment/`.
- Documentation: `README.md` for humans getting started, `docs/` for runbooks,
  architecture, and ADRs; `AGENTS.md` for agents (a map, not an encyclopedia).
- One compose file name per repository (`compose.yaml`), with environment overrides
  as separate files.
