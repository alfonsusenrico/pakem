# Changelog

All notable changes to this framework are recorded here. Versions follow semantic
versioning as defined in `AGENTS.md`.

## [Unreleased]

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
