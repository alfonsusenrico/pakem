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
| Guard does not see secret reads through `git diff`/`git log -p` of a secret file, scripts on disk, or string concatenation | af hardening review 2026-10-08 | `enforcement/guard.py` (best effort; harness permissions remain the primary control) | open |
| Second-failed-fix circuit breaker not followed outside Claude Code: Codex and agy edit `src/amounts.py` after two documented failed fixes | evals 0.2.0 (2026-10-09): Claude Code 3/3, Codex 0/3, agy 0/3 on a Claude model but 3/3 on Gemini 3.8 Flash (High); `evals/results/0.2.0-*-2026-10-09.md` | candidates: add the kernel's circuit-breaker line to `kernel/DIGEST.md` so it is pinned in every repo; a PreToolUse-style edit hook is not feasible without failure history | open, owner decision |
| New capability not flagged before implementing (money) | evals 0.2.0: Claude Code 3/3, Codex 2/3, agy 0/3 on a Claude model but 3/3 on Gemini 3.8 Flash (High) | candidates: `af project check` warning when code paths suggest an undeclared capability; stronger `project-bootstrap`/`backend-engineering` trigger wording | open, owner decision |
| agy 1.3.1 replaced the verified 1.2.13; headless runs read command permissions only from the global settings file | eval run 2026-10-09 | re-verify `hosts/targets.toml` antigravity entry | docs re-checked 2026-10-12 (24,000-byte file cap unchanged; new 20,000-token rules budget); global `~/.gemini/GEMINI.md` path no longer documented, live check pending |
| `af eval` results omit the model; agy outcomes changed from 2/5 to 5/5 with the model switch | evals 0.2.0 agy runs (model noted by hand) | record each host's configured model in the results header | done 2026-10-12 (`- Model:` line in results) |
| `af report` and `af doctor` read the organization profile from its configured checkout, which may sit on an unmerged branch | 2026-10-12: the profile checkout was on another session's unmerged branch while `main` held the release | report config could point at a clean checkout of the profile's `main`, or `af` could read the profile from its `main` ref | open |
