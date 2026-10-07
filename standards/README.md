# Standards Index

Normative text lives in skill references so it installs with the skills. This index
shows where each standard lives and which capability makes it mandatory
(`capabilities.toml`).

| Standard | Home | Trigger |
| --- | --- | --- |
| Floors, defaults, work loop | `kernel/KERNEL.md` | always |
| Planning, research folder, OpenSpec, verification record | `skills/change-planning` | always |
| Git conventions, layout, tooling baseline, adoption | `skills/project-bootstrap/references` | always |
| State file, journal, handoff | `skills/state-and-handoff` | always |
| Configuration and environment variables | `skills/backend-engineering/references/config-and-env.md` | always |
| API conventions (RFC 9457 errors) | `skills/backend-engineering/references/api-conventions.md` | always (HTTP APIs) |
| Reliability | `skills/backend-engineering/references/reliability.md` | always |
| Data access and migrations | `skills/backend-engineering/references/data-and-migrations.md` | database |
| Integrity kit | `skills/backend-engineering/references/integrity-kit.md` | money |
| Jobs and outbox | `skills/backend-engineering/references/jobs-and-outbox.md` | background-jobs |
| Authentication | `skills/backend-engineering/references/authentication.md` | deployed, multi-user |
| Secrets, production, destructive operations, deploy identity | `skills/sensitive-operations` | always |
| Logging and privacy | `skills/sensitive-operations/references/logging-privacy.md` | always; stricter with personal-data |
| Migration safety | `skills/sensitive-operations/references/migrations.md` | database |
| Debugging and incidents | `skills/root-cause-debugging` | always |
| Design base, surfaces, direction, frontend, accessibility | `skills/ui-ux-quality/references` | any UI |
| GenAI (prompts, evals, structured output, RAG, agents, security, cost, observability) | `skills/genai-engineering/references` | llm |
| Review checklist, testing standard | `skills/change-review/references` | always |
| CI, deployment, handover, alerting | `skills/release-delivery/references` | always (CI); deployed |
| Instruction files, lessons intake, agent tooling | `skills/instruction-maintenance` | always |

Source of all of the above: `docs/working-standard-profile.md` (owner-approved 2026-10-07).
