# Changelog

All notable changes to this framework are recorded here. Versions follow semantic
versioning as defined in `AGENTS.md`.

## [Unreleased]

- Design bases for tools: `owner-dashboard` and `owner-workbench` (both themes, measured
  contrast, provenance), from the portfolio (primary), the finance tracker web UI, and
  the office dashboard and workbench UIs. Status: Draft pending owner review.
- Uppercase labels use +0.05 to +0.08em tracking (research-backed; Butterick).

- Worktrees live next to the repository, never under `/tmp`; local state files are per
  checkout and belong in the owner's main checkout.
- First push of a bootstrapped `main` is an owner action with `AF_ALLOW_PROTECTED_PUSH=1`.
- Archive older OpenSpec changes in dependency order with per-command status checks and
  real Purpose sections.

- OpenSpec gate is `openspec validate --all` (errors fail); `--strict` warnings are recorded as follow-up work (owner decision).

- Adoption lessons from the first migrations: compare specs before deleting AGENTS.md text;
  commit only from the adoption worktree while hooks are unmerged; separate OpenSpec
  warnings from errors after CLI upgrades; no wrapper scenarios; gate commits on check
  exit status, never on piped output.

- Project AGENTS.md template declares the project's own language rule (`Language:`); the framework and profiles set no language default.

### Added

- Owner-approved Working Standard Profile (discovery across office, freelance, and
  personal projects plus a five-round interview).
- Kernel and digest: precedence, floors, capability triggers, crunch mode, agent
  autonomy, research folder, capped state file, research-backed defaults.
- Capability registry (`standards/capabilities.toml`) and standards index.
- Skills: `root-cause-debugging`, `change-planning`, `sensitive-operations` (with
  keys-only `env_keys.py`), `backend-engineering`, `ui-ux-quality` (design base,
  surfaces, owner-studio base), `project-bootstrap` (project templates),
  `state-and-handoff`, `genai-engineering`, `change-review`, `release-delivery`
  (handover templates), `instruction-maintenance`.
- `af` tool: install, uninstall, doctor, lint, project sync/check, hooks, enforce.
- Git hooks for subject-only Conventional Commits and the documentation-only
  `main` exception.
- Enforcement fragments for Claude Code, Codex, and Antigravity CLI.
- ADRs 0001-0005, host matrix, migration mapping, framework backlog.
- Configurable protected branches: the multi-valued git config key
  `agentframework.protectedBranch` (default `main`, `master`), set with
  `af hooks install --protect BRANCH` (repeatable, replaces the set).
- `af hooks install --local-only` adds `.githooks/` to `.git/info/exclude` so client
  repositories never receive committed hook files.
- `AF_ALLOW_PROTECTED_PUSH=1` as the documented pre-push override
  (`AF_ALLOW_MAIN_PUSH=1` still works).

### Changed

- The pre-push documentation-only rule and the Claude Code / Antigravity guard now
  protect the repository's configured branches instead of only `main`/`master`; the
  guard reads the config in the push's working directory and falls back to
  `main`/`master` when it cannot.
- Codex execpolicy rules also forbid pushes to `development`, since static rules
  cannot read per-repository config.
