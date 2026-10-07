# Native OpenSpec Workflow

Read this guide for meaningful features, behavior changes, architecture changes,
and substantial corrective work requiring durable planning.

## Applicability

- Initialize native OpenSpec for a new development project before meaningful
  implementation when project setup is authorized.
- Direct documentation corrections and trivial mechanical changes need
  proportional verification rather than a planning ceremony.
- For corrective work with an unknown cause, plan reproduction, instrumentation,
  and diagnosis without pretending the correction is already known.

## Native tooling authority

- Inspect the installed CLI, selected profile, project configuration, and generated
  instructions.
- Use the native `core` profile by default. A custom profile requires an explicit
  project need and documented reason; do not remove core workflows merely to
  shorten the command set.
- Treat generated skills and commands as tool-owned adapters rather than project
  source. Create and refresh them using native `openspec init` and
  `openspec update`; never hand-author, fork, or imitate `openspec-*` skills or OPSX
  prompts.
- The agent-facing generated workflow is the entry point. The installed CLI is
  authoritative for scaffolding, status, dependencies, schemas, instructions,
  validation, synchronization, and archive behavior.
- Query the CLI rather than reconstructing native rules from memory or an
  "OpenSpec-style" method. Do not claim native OpenSpec use when the CLI did not run.

## Workflow selection

- Use exploration to resolve uncertainty.
- Use propose to prepare concrete, reviewable artifacts.
- Use apply to implement approved work.
- Use update whenever approved scope or design changes.
- Use sync and archive when native status and instructions make them appropriate;
  not every workflow action is required on every turn.

## Artifact quality

- Read native status and artifact instructions before creating or revising an
  artifact. Follow the native schema and build order.
- Explain expected outcome, scope and non-goals, relevant current behavior,
  assumptions, affected boundaries, and failure behavior.
- Define implementation tasks and observable acceptance evidence.
- Include compatibility, migration, recovery, and rollback decisions when relevant.
- Keep requirements testable and preserve important scenarios, constraints, and
  rationale in normal prose. Do not substitute a vague checklist for a plan.

## Approval and change control

- Present meaningful changes for approval before implementation.
- Once approved, execute without repeatedly requesting the same authorization.
- Surface material changes to scope, behavior, compatibility, or acceptance before
  implementing them.
- Update active artifacts as approved decisions evolve.
- Mark tasks complete only when specified implementation and evidence are complete.
  Artifact existence and checked boxes do not prove application correctness.

## Validation, synchronization, and archive

- Run native OpenSpec validation for affected changes and verify application
  behavior separately. Validation proves artifact structure and consistency, not
  implementation correctness.
- Archive accepted work only after verifying that intended deltas reached durable
  specifications. Do not edit archived decisions as a shortcut for a new change.
- Project-local instructions may add repository-specific context and verification
  commands but should reference this baseline instead of redefining the native
  workflow.
- Keep generated integrations current and remove obsolete duplicate command or
  skill copies.
- If native tooling is unavailable, disclose the limitation. Use a clearly labeled
  manual fallback only when necessary; never claim validation or synchronization
  that did not run.
