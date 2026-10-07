# Framework Backlog

Candidates waiting for owner review or a later phase (see
`skills/instruction-maintenance/references/lessons-intake.md`).

| Item | Evidence / reason | Proposed home | Status |
| --- | --- | --- | --- |
| Deployment pipeline templates and generic release scripts (release host, deploy driver with hooks, manifest-driven runtime materializer, smoke, tunnel config) | CI study 2026-10-07; `skills/release-delivery/assets/ci/DEPLOY_DESIGN.md` | `release-delivery` assets | Phase 2b, needs approval and a sandbox host |
| Design bases for tools (dashboard, workbench) | owner's private reference | `ui-ux-quality/references/design-bases/` | Phase 5 |
| Organization profile for the current employer (platform integration, release pattern) | review of office repositories | separate profile repository | Done 2026-10-07 (kept outside this repository) |
| Personal/freelance profile and workspace map | owner workspaces | `profiles/personal` | Phase 5 |
| Shared versioned platform client for an organization hub | duplicated drifting copies across repositories | organization profile follow-up | tracked in that profile |
| Compliance eval scenarios per host (commit format, secret read, second failed fix, state update, UI review) | factorial study on instruction adherence | `evals/` | Phase 2 |
| Guard gaps: `git show HEAD:.env`, interpreter file reads, variable indirection | tooling review | `enforcement/guard.py` | open |
| `af install` prune of skills removed from the framework; install transaction | tooling review | `tools/af_lib/installer.py` | open |
