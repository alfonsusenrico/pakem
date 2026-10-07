# Working Standard Profile

Status: Approved by the owner on 2026-10-07
Last reviewed: 2026-10-07
Sources: owner interview (5 rounds, 2026-10-07); evidence from office projects (a shared
AI backend hub and two AI analysis products: a data dashboard and a task workbench) and
owner-selected outside projects (a freelance marketplace backend, the owner's portfolio
and personal finance tracker, a client event-reservation system, client headless
WordPress landing pages, a proposal generator, a community scheduling tool); cited research.

This profile is the owner-approved source from which the kernel, skills, standards, and
templates are written. It records how the owner works, not any project's business rules.

## 0. How evidence was weighted

- Structure comes from office work, where the owner learned to work from frameworks and
  baselines. Outside projects mostly predate the framework; they emphasize, fill gaps, or
  improve it, and never override it.
- Reference roles: the freelance marketplace backend for backend engineering; the
  portfolio as the primary UI/UX reference (the office dashboard and workbench UIs
  secondary); the finance tracker for problem-driven product evolution and LLM features;
  the reservation system for complex domain backends under extreme time pressure
  (patterns, not structure); the headless WordPress sites for fitting a solution to a
  cost-conscious client; the proposal generator for proposal thinking.
- The framework keeps growing with every project. Its purpose is to help as many people,
  businesses, and problems as possible.

## 1. Principles

1. Correct, verified outcomes over speed of typing; evidence over claims.
2. Where taste or convention is uncertain, the default comes from proven, credible, current
   research on user outcomes, with sources recorded.
3. Rigor follows what a project does, not its size (section 2).
4. Decisions are made by the owner; agents bring options, a recommendation, and evidence.
5. Lessons from any project flow back into the framework through a defined intake path
   (section 14).

## 2. Rigor model: capability triggers

Standards attach to capabilities. Each project lists its active capabilities in
`AGENTS.md` (for example `Capabilities: deployed, money, personal-data`), and the list
grows with the project.

**Always (from the first commit)** — chosen because they are cheap on day one and expensive
to retrofit: git with Conventional Commits; OpenSpec planning; typed fail-fast
configuration; thin layering; versioned migrations when a database exists; schema
validation at boundaries; formatter + linter + CI; no secrets in code; `research/` folder;
`PROJECT_STATE.md`.

| Capability trigger | Standards that switch on |
| --- | --- |
| `deployed` (used by others) | request id + safe structured logs; security scans in CI; immutable releases, health check, rollback; backups; both themes for UI |
| `money` (payments, bookings, inventory) | integrity kit: idempotency keys, append-only audit events (before/after, actor), amount and price snapshots, row/advisory locks; critical-path tests; webhook signature verification; auth on admin and money routes |
| `personal-data` | privacy and logging rules, retention, access control, synthetic fixtures |
| `background-jobs` | DB-backed jobs/outbox claimed with `FOR UPDATE SKIP LOCKED`, separate worker, idempotent handlers |
| `api-consumers` (separate FE team, mobile, third parties) | OpenAPI generated from code as the contract, generated clients, drift check in CI |
| `multi-user` (roles) | authorization model, audit trail, auth requirements (section 6) |
| `llm` | GenAI standards (section 11) |
| `database` | real disposable Postgres in tests and CI, migration re-apply check |

**New-trigger rule.** When a change adds a capability, the agent must say so before
implementing. The change then either includes the newly required standards or records the
gap as debt with a payback plan.

**Crunch mode** (temporary, declared by the owner). Non-negotiable even in a crunch: kernel
floors; data and money correctness (including auth on admin/money routes and webhook
signature checks); critical-path tests. Everything else may be deferred only as recorded
debt with a payback plan.

## 3. Workflow: idea to delivery

The sequence below is assembled from evidence (the marketplace backend, portfolio, finance
tracker, office projects) and the owner's answers.

1. **Intake** — client request, own problem, or org assignment. Agent restates outcome,
   constraints, non-goals.
2. **Brainstorm / discussion** — the agent acts as interviewer (focused questions until
   requirements are clear, then summarizes for confirmation), researcher (prior art, current
   docs, existing code first), challenger (assumptions, risks, contradictions), and advisor
   (2–3 options with trade-offs and a clear recommendation). Constraint fit (budget,
   deadline, who maintains it, hosting capability) is raised by judgment, not as a
   mandatory section.
3. **Research and blueprint** — notes, audits, prototypes, and evidence go in `research/`
   (`research/notes/YYYY-MM-DD-<topic>.md`, `research/prototypes/`, `research/evidence/`);
   a phased plan with verification gates for larger work.
4. **Spec** — native OpenSpec on every project: proposal (Why, Expected Outcome, What
   Changes, Impact), design (decisions with rejected alternatives, risks, migration), spec
   deltas (SHALL + WHEN/THEN), tasks where each task names its verification evidence and
   each phase has an Expected Result.
5. **Approval gate** — the owner approves before implementation.
6. **Implementation** — topic branch; autonomy per section 4.
7. **Verification** — evidence per task; `verification.md` per change (commands, counts,
   environments, what was not verified); no overclaiming.
8. **Delivery** — push branch and report the compare link; the owner merges; pipeline
   deploys; owner acceptance.
9. **Archive** — sync specs and archive once evidence is verified; owner-side production
   confirmation is tracked in `PROJECT_STATE.md` ("Awaiting owner confirmation") and does
   not block archiving.
10. **Lessons intake** — section 14.

UI direction decisions use side-by-side working alternatives (section 8.4).

## 4. Git, autonomy, and continuity

- Conventional Commits, subject only: `type(scope): summary`, types feat, fix, docs,
  refactor, test, chore, ci, perf, build, revert; no body or trailers (hook-enforced).
- Branches `<type>/<kebab-topic>`; agents never merge; direct push to `main` only for
  documentation-only ranges (hook-enforced).
- Within approved work an agent may, without asking: create the branch and commit; push the
  branch when checks pass and report the compare link; run local infrastructure on loopback
  and stop it afterwards; add project-local dependencies with lockfile updates.
- Always ask: production, secrets, merging, deleting data, global installs, scope changes.
- `PROJECT_STATE.md`: local, untracked, capped (~8 KB, fixed sections); dated history goes to
  a local `JOURNAL.md` or per-change `verification.md`; durable rules go to `AGENTS.md`/specs.

## 5. Architecture and backend

- Default stack: Python + FastAPI + uv (especially AI services); NestJS when the client or
  team works in TypeScript; Next.js route handlers only for small full-stack apps; headless
  CMS when non-developers must edit content cheaply.
- Layering: thin routes/controllers → services (domain logic; pure helpers unit-tested) →
  data layer. Files over ~800 lines are split.
- Data access: SQL-first thin data layer — parameterized SQL in per-domain query modules,
  explicit transactions and locks, never SQL in route handlers.
- Migrations: hand-written, idempotent, forward-only, checksummed, no autogenerate; run as a
  separate step that must succeed before new app instances start.
- API shape: plain resources (lists `{items, meta}`); errors as RFC 9457 problem+json with a
  stable `code`, `request_id`, and field `errors[]`.
- Validation: schema library at every boundary (Pydantic, class-validator with whitelist +
  forbid unknown, zod); unknown fields rejected.
- Configuration: one typed settings object validated at startup, fail fast; `.env.example`
  lists every name; UPPER_SNAKE with component prefix and unit suffix.

## 6. Security and privacy

- Kernel floors always apply (secrets, production, destructive operations, deploy identity,
  no bypassing controls, truthful reporting).
- Auth requirements for any mechanism: expiry, revocation, CSRF protection with cookies,
  constant-time secret comparison, no long-lived tokens in script-readable storage.
  Defaults: HttpOnly cookie session for own web apps; JWT bearer with token-version
  revocation for APIs consumed by separate frontends or mobile apps.
- Logs: ids, counts, states, error categories; never personal data, prompts, model output,
  or credentials.
- Production data: synthetic or anonymized fixtures by default; a real dump only for a
  specific diagnosis, outside the repo tree, encrypted at rest, with a recorded deletion date;
  agents never read it unless asked for that task.

## 7. Frontend engineering

- Design tokens (CSS custom properties) are mandatory; hand-written CSS, Tailwind, or a
  component library is a project choice.
- Prettier (default options) for JS/TS/CSS/Markdown; CI-checked.
- Shared primitives in `components/ui`, feature folders; components over ~400 lines split.
- Phone layouts are purpose-built (navigation, density, disclosure), tested at 320/390 and up.
- Data fetching and form libraries are project choices.

## 8. UI/UX design base

### 8.1 Invariants
- Radius: controls 7–8px; grouped containers 10–16px; full pill only for chips, choices,
  and round icon buttons; table rows and badges 4–6px.
- One interactive accent per product (chosen per project); semantic colours only for real
  state; never colour alone.
- Hierarchy through alignment, type, and 1px hairlines; cards only for selectable or
  interactive items; no decorative or celebratory cards.
- Type weight (research-backed): headings and KPI values 600 (500–650 by typeface); body
  400; small text ≤12px may use 500–600; never ≥800 for UI text; nothing below 400;
  tabular figures for numbers in tables and KPIs.
- Uppercase only for table column headers and field labels, ≥11px and ≥4.5:1 contrast;
  never as eyebrows on public pages.
- Prose left-aligned, 60–78ch, line-height ~1.6; no captions that lead to no action.
- Line icons ~1.6–1.8 stroke, round caps, custom or outlined; brand logos filled.
- Truthful content: no invented metrics or outcomes; unknown is never shown as 0;
  progress only from real counts.
- Native semantics first (`<dialog>`, `<details>`), Escape and focus return, visible 2–3px
  focus outlines, ~44px targets, WCAG 2.2 AA as the target without conformance claims.
- Both light and dark themes on every surface (persisted, following the OS until changed).
- AI surfaces: no sparkle or AI-brand icons, no glow; user messages may be bubbles, assistant
  answers are plain text blocks; a custom assistant mark per product.

### 8.2 Fonts
- Marketing sites self-host an expressive pair; tools may self-host one family or pair when it
  strengthens identity (max 2 families, only used weights); otherwise system stacks.

### 8.3 Surfaces
| | Marketing / portfolio | Tools: dashboards and workbenches |
| --- | --- | --- |
| Depth | soft shadows allowed, tinted (never black) | flat; elevation only for dialogs, menus, popovers |
| Motion | ambient and auto-rotating motion only with safeguards: pause offscreen, manual choice resets the interval, global pause control, reduced motion | functional only: feedback 120–350ms, transitions up to ~650ms, no ambient loops |
| Layout | airy, asymmetric, full-bleed bands | dense, aligned, master–detail or sidebar + toolbar |

Primary reference values (portfolio): cream `#fffaf5`, navy ink `#243b49`, forest accent
`#28594e`, terracotta decorative `#d87747`, sage selected `#dbe6d3`, Outfit 600 + DM Sans,
1160px wrap, easing `cubic-bezier(.22,1,.36,1)`.

### 8.4 Choosing a direction
For a new direction or major redesign: build 2–4 working alternatives (no-index review route
or `research/prototypes/`), compare in the browser, record the choice and rejected options.
After acceptance: refine, don't restyle.

## 9. Testing and QA

- Code that touches the database is tested against a real disposable Postgres with real
  migrations; CI fails if database tests skip. Mocks only for pure logic and external services.
- Reusable verification (HTTP flows, browser checks, concurrency, migration checks) is
  versioned in the repo (`tests/e2e/`, `scripts/verify/`) with documented commands; outputs
  go to `research/evidence/`; nothing durable lives in `/tmp`.
- No placeholder tests; every test protects a real behaviour.
- UI evidence: widths 320–1920, 200% text, text spacing, keyboard, axe and measured contrast.

## 10. CI/CD and deployment

One default template, expressed for GitHub Actions (reusable workflows) and GitLab CI
(`include` + `extends`):

- **Always**: lockfile-checked install; format check; lint; typecheck; tests; build;
  secret scan; dependency audit; CI/shell/Dockerfile lint; least-privilege tokens; actions and
  images pinned.
- **`database`**: Postgres service, tests may not skip, migrate up then re-apply as no-op,
  drift check.
- **`deployed`**: image build with revision label, image scan, SBOM; `main` → staging;
  `v*` tag on `main` → production behind a manual gate; one deploy per environment at a time;
  immutable release per SHA (digest-verified), in-image preflight, isolated candidate check,
  pre-migration backup in production, migrate, activate with automatic restore on failure,
  revision-matching health check locally then publicly, cleanup after smoke; manual rollback
  for every environment; notifications without secrets.
- Runtime configuration: committed per-environment defaults + required/optional variables +
  declarative production assertions; host-generated secrets never travel back to CI.
- Client-operated hosting: a versioned handoff package (artifact, runbook, preconditions/
  seed/cleanup SQL) built from a tagged commit. Never deploy loose files or zips of
  uncommitted work.

## 11. GenAI features (`llm` trigger)

- Always: prompts as versioned files (EARS wording for behavioural rules), explicit model id
  per call, prompt and output content never logged; evals with a development set and a
  held-out set re-run before any model, prompt, or provider change ships.
- When code consumes model output: strict structured output validated in code, bounded
  repair or retry, fail closed; deterministic code computes facts, the model interprets.
- When a model decision has consequential side effects (money, messages, records):
  confidence gate with human confirmation; dry-run mode for new pipelines.
- Agent architecture (plain tool loop, LangGraph, other) is a project decision.
- Security reference: OWASP Top 10 for LLM Applications 2026 and OWASP Agentic Top 10.

## 12. Client delivery

Every handover includes: a runbook in the client's language (install, deploy, operate,
rollback; environment variable names and how to obtain each secret); a user or content-editor
guide from a reusable template in the client's language; generated API/integration docs.
Process artifacts (`AGENTS.md`, `PROJECT_STATE.md`, `openspec/`, `research/`) stay private
unless the client buys the process. Code, logs, and engineering docs are in English;
client-facing copy follows the client's language.

## 13. Agent hosts

First-class: Claude Code, Codex, Antigravity CLI. Others via AGENTS.md and Agent Skills.

## 14. Framework growth (lessons intake)

Incident or discovery in a project → project rule (`AGENTS.md` or spec) → if general and not
discoverable from code, a framework candidate noted in the framework's backlog → owner review →
ADR when it is a decision → released with a CHANGELOG entry and version bump → projects pick it
up through `af project sync`. Rules are pruned as deliberately as they are added.

## 15. Notes

- Approved as written, including section 3 (larger work gets a phased implementation plan
  with verification gates before its OpenSpec proposals) and the capability names and
  "always" set in section 2.
- Portfolio is light-only today; it adopts the both-themes rule at its next redesign.
