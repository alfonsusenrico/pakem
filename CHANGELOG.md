# Changelog

All notable changes to this framework are recorded here. Versions follow semantic
versioning as defined in `AGENTS.md`.

## [Unreleased]

## [1.0.0] - 2026-10-12

### Changed

- Pushes to protected branches are decided by the repository's pre-push hook: Claude
  Code deny rules, the guard, and Codex rules no longer block plain pushes to `main`;
  they still block force-pushing or deleting protected branches, and other force
  pushes need the owner.
- `af hooks install --protect-none` for projects whose `## Overrides` allow direct
  pushes; `af project check` warns when the framework hooks are not active.
- Kernel floors: ask before deleting data or files the current task did not create;
  agents never merge or approve a merge request (ADR 0006).
- `default:mr-policy`: agents push the topic branch, open the merge request, and report
  its URL. `default:branching`: `main` stays deployable, documentation-only changes may
  go directly to `main`, and a project may allow more.
- `backend-engineering`: unknown fields are rejected on inputs the project defines;
  third-party responses and webhooks are validated on the fields used. Applied
  migrations must not change silently (runner checksum or a CI comparison), so
  runners without checksums qualify with that CI test.
- Light and dark themes apply to every UI; removed from the `deployed` capability.
- `owner-dashboard` and `owner-workbench` are marked "do not adopt until the owner
  approves it".
- Planning and bootstrap templates: the last task opens the merge request and
  reports its URL; force pushes need the owner.

### Removed

- `db_prod.txt` from the Claude Code deny list and the guard's secret names
  (workspace-specific; the production-dump floor stays).

### Fixed

- `af eval run --host antigravity` passes `--dangerously-skip-permissions` (with
  `--sandbox`): headless agy ended every run at the first command outside the global
  allow list, so command-dependent scenarios measured the harness, not compliance.
- `af eval run --host codex` adds the fixture's own `.git` as a writable sandbox root,
  so commit-format runs can commit; `workspace-write` otherwise keeps `.git` read-only
  and every commit failed on `.git/index.lock`.

## [0.2.0] - 2026-10-09

### Added

- Change fragments: each branch records its change as `changes/<type>-<kebab-topic>.md`
  (Keep a Changelog types; Markdown bullets) instead of editing `CHANGELOG.md`, so
  concurrent branches no longer conflict (`changes/README.md`).
- `af changelog [--release X.Y.Z] [--date YYYY-MM-DD] [--apply]` shows pending entries
  grouped by type, or prints the release section; `--apply` inserts
  `## [X.Y.Z] - DATE` under an emptied `## [Unreleased]`, moves legacy Unreleased
  bullets into it, deletes the consumed fragments, and writes `VERSION`. It refuses a
  version not greater than `VERSION`, a malformed date, an empty release, and invalid
  fragments.
- `af lint` fails on bullet entries under `## [Unreleased]` in `CHANGELOG.md` and on
  fragments with an unknown type, a bad name, or no bullet (`changes/README.md` is
  exempt).
- Configurable protected branches: the multi-valued git config key
  `agentframework.protectedBranch` (default `main`, `master`), set with
  `af hooks install --protect BRANCH` (repeatable, replaces the set).
- `af hooks install --local-only` adds `.githooks/` to `.git/info/exclude` so client
  repositories never receive committed hook files.
- `AF_ALLOW_PROTECTED_PUSH=1` as the documented pre-push override
  (`AF_ALLOW_MAIN_PUSH=1` still works).
- Profile `personal` 0.1.0 in `profiles/personal/`: digest for personal and freelance
  repositories, skills `freelance-delivery` and `client-engagement`, and the personal
  workspace map (profile ADR 0001; its own `CHANGELOG.md`).
- Deploy templates (Phase 2b) in `skills/release-delivery/assets/deploy/`:
  `deploy_remote.sh` (CI, POSIX sh: validates every input before SSH, archives the exact
  commit, pinned `known_hosts`, own key only, host sequence under the environment lock,
  public smoke, automatic restore on a failed promote or smoke), `release_host.py` (host:
  `prepare | secure-file | lock | activate | promote | rollback | cleanup | record |
  status`; owner modes `root` and `self`; safe extraction; immutable releases verified by
  tree digest; per-commit image build verified by image id and OCI revision label;
  preflight, isolated candidate check, production backup hook, migrations before `up`,
  local revision check, automatic restore; retention 3/6 without touching running
  releases; `deployment.json` record), `materialize_runtime.py` (manifest-driven 0600
  runtime: per-environment defaults, required, optional, pairs, formats, host-generated,
  production assertions; values never printed), `smoke.py`, a README, and example
  `release.toml` and `runtime.manifest.toml`. CI: GitHub reusable workflow
  `deploy.yml` (environment, sha, mode; per-environment concurrency; Tailscale action
  pinned at v4.2.0 by SHA) with `deploy.example.yml`; GitLab hidden jobs `.af:deploy`,
  `.af:rollback`, `.af:smoke` (no `resource_group`; production only on protected `v*`
  tags with `when: manual`), wired into `gitlab/ci.example.yml`. `DEPLOY_DESIGN.md` is
  Active with its deviations recorded. Tests: `tests/test_deploy_*.py`, deploy checks in
  `tests/test_ci_templates.py`, and an opt-in Docker sandbox
  (`AF_SANDBOX=1 python3 -m unittest tests.test_deploy_sandbox`).
- `af eval run --host claude|codex|antigravity [--scenario ID ...] [--repeat K]
  [--timeout S] [--apply] [--keep-fixtures] [--results-dir DIR]` measures kernel
  compliance per host with five scenarios in `evals/scenarios/` (commit-format,
  secret-read, capability-trigger, circuit-breaker, state-update) and deterministic
  graders. Dry run by default (plan, exact command, timeout, cost note; writes
  nothing). `--apply` builds a fresh fixture repository per repeat with a per-run dummy
  token, runs the host CLI non-interactively with a timeout, grades output and
  repository state, fails and redacts any token leak, and appends pass^k and pass@k to
  `evals/results/<VERSION>-<host>-<date>.md`. Live runs are owner-approved, before each
  minor release (`evals/README.md`).
- `af report [--config FILE] [--output-dir DIR] [--keep N]` writes a read-only Markdown
  drift report: framework source (branch, commit, match with the local `origin/main` ref,
  no fetch), `af lint`, `af doctor` installed state, and `af project check` per repository
  from a TOML config (`repos_dirs`, `repos`, `profiles`, `keep`; template
  `docs/report.example.toml`). It writes `YYYY-MM-DD-HHMM.md` and `latest.md` under
  `~/.local/state/agent-framework/reports/`, prunes dated reports beyond `keep`, records
  findings only (never file contents), and exits 0 whenever the report was written.
- `af schedule install|uninstall|status` (macOS only) manages a launchd agent
  (`com.agentframework.drift-report`) that runs `af report` weekly (default Monday 08:00).
  Dry run by default; `--apply` writes the plist and runs `launchctl bootstrap`/`bootout`.
  The plist is recorded in the manifest, so uninstall removes only an unedited plist af wrote.
- Design bases for tools: `owner-dashboard` and `owner-workbench` (both themes, measured
  contrast, provenance), from the portfolio (primary), the finance tracker web UI, and
  the office dashboard and workbench UIs. Status: Draft pending owner review.
- `af install` prunes skills it installed whose framework or profile source no longer
  exists (`remove-managed`; an edited copy blocks unless `--force` backs it up). Skills
  of profiles not passed with `--profile` are kept and listed as `kept`. Only the
  selected hosts' skills directories are pruned. `af doctor` warns about orphaned
  managed skills.
- `af install --apply` is transactional: when a write or the manifest save fails, every
  target written in the run is restored (originals are set aside, not deleted, until the
  run succeeds), newly created files and directories are removed, the manifest is left
  unchanged, and the rollback is reported.

### Changed

- `af project check` warns about a digest block only when its body differs from the
  framework's current `kernel/DIGEST.md`; an older `version` attribute on an unchanged
  body is no longer a warning, so releases that leave the digest unchanged do not make
  adopted repositories warn.
- Uppercase labels use +0.05 to +0.08em tracking (research-backed; Butterick).
- `af install --apply` installs from merged `main` only: it refuses a checkout on another
  branch or a detached HEAD, or one whose HEAD differs from the local `origin/main` ref
  (no fetch; the message says to fetch to refresh), unless `--allow-unmerged` is given.
  `--allow-dirty` still covers uncommitted changes and non-git roots; the two waivers
  are independent. A dry run prints the same findings as warnings. Git profiles given
  with `--profile` are checked for a clean `main` as warnings, or refusals with the new
  `--strict-source`.
- Guard (Claude Code, Antigravity) denies secret reads through `git show REV:PATH`,
  `git show REV -- PATH`, `git cat-file ... REV:PATH`, interpreter one-liners (python -c,
  node -e/--eval/-p, ruby -e, perl -e, php -r, deno eval, bun -e) that name a secret path
  in a string literal or argument, and secret paths assigned to a shell variable and
  read through `$VAR` later in the same command line.
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
- The pre-push documentation-only rule and the Claude Code / Antigravity guard now
  protect the repository's configured branches instead of only `main`/`master`; the
  guard reads the config in the push's working directory and falls back to
  `main`/`master` when it cannot.
- Codex execpolicy rules also forbid pushes to `development`, since static rules
  cannot read per-repository config.

## [0.1.0] - 2026-10-07

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
