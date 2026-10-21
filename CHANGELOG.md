# Changelog

All notable changes to this framework are recorded here. Versions follow semantic
versioning as defined in `AGENTS.md`.

## [Unreleased]

## [2.0.1] - 2026-10-21

### Added

- `pakem project check` warns when `AGENTS.md` has no `Delivery:` line (or an unknown
  value), and when an `llm` project has no `default:no-billed` override recording its
  paid services or that it uses none.

### Fixed

- `pakem hooks install` updates earlier versions of its own hooks without `--force`
  (recognised by checksum); hooks edited in a repository still need `--force`.
- The personal profile (0.3.0) declares Pakem 2.x, so `pakem install --profile
  profiles/personal` works with 2.0 again.

## [2.0.0] - 2026-10-21

### Added

- Projects declare `Delivery: us | others (<who>) | nobody` in `AGENTS.md`. With
  `others`, agents stop at commits, branches, and the merge request and follow the
  team's release process; `release-delivery` applies in full only with `us`. A missing
  line is asked as a gap.
- `release-delivery`: deployment contracts v1 (draft, ADR 0009): release record, deploy
  bundle, release pointer and signatures, project files, engine interface, deployment
  record, host layout, promotion, and the migration rule; `assets/deploy/contracts.py`
  validates them, with examples in `assets/deploy/contracts/`.
- `af_bootstrap.sh` prepares a Debian or Ubuntu host for the deployment standard: Docker
  Engine with Compose and Caddy from their signed apt repositories (key fingerprints
  pinned), cosign as a checksum-pinned binary, the `af` deploy user, the engine in `/opt/af`,
  `/etc/af`, the deploy root, Caddy's admin socket, and the agent and monitor timers. It
  is idempotent, keeps an existing Docker and a foreign Caddyfile untouched, and ends with
  a `check` that can be rerun any time.
- Kernel default `default:no-billed`: agents use only free services and free tiers; a
  project approves a specific billed service, with its budget, in `## Overrides`.
- ADR 0010 (Accepted): builders and deployment triggers are each project's choice (local
  device, self-hosted GitLab, free GitHub runners), checks run separately from releases,
  and a direct delivery mode sends signed releases over a restricted SSH receiver.
- Skill `pakem-update` (`/pakem-update` in Claude Code): a running session re-reads the
  installed kernel and the skills it uses, reports what changed, and aligns its own
  project once with judgment by whose project it is (own, work, client): text fixes or
  proposals, never refactors, overrides respected, nothing skipped silently; commits locally;
  pushing, installs, deletion, and production still need the owner.
- `change-planning`: gap interviews with a firm finish (one batch with defaults, at most
  two rounds, every gap answered, provisional, or pending).
- Zero-downtime `blue-green` switching in the release engine (opt-in per project): routed
  services start beside the running ones on ephemeral loopback ports, the new containers
  must pass the health check, the host proxy moves to them, and only then do the old ones
  stop; workers are recreated afterwards. A failure before the switch removes only the new
  containers and leaves the old release serving. Requires a host `[proxy]` and
  migrations the previous release can run against (contracts §9).
- `release-delivery` engine (contracts v1, draft): `af_oci.py`, a standard-library OCI
  registry client that verifies every manifest and blob against its digest, and
  `af_release.py`, which resolves an environment pointer, checks cosign signatures on the
  record, images, and bundle, refuses older releases without the rollback flag, and unpacks
  the deploy bundle (files and directories only, no `.env` files).
- Opt-in sandbox test (`AF_SANDBOX=1`) publishes and verifies a release with a real
  registry, oras, and cosign 3.
- `release-delivery` engine (contracts v1, draft): `af_engine.py` runs the deployment
  sequence under a lock (verify, pull by digest, settings, preflight, drain, backup,
  migrate, start, revision health check, hooks, promote, retention with image cleanup,
  deployment record) and on failure restores the previous release only when the schema is
  unchanged, otherwise leaves services stopped (exit 5); `deploy`, `status`, and
  `secret-set` commands with a private host configuration.
- Contracts: `health.port_setting` names the placement value holding the loopback port the
  engine checks.
- Opt-in end-to-end test (`AF_SANDBOX=1`) deploys real images through a real registry and
  cosign: install, upgrade with a backup, and automatic restore of a broken release.
- `release-delivery` engine (contracts v1, draft): `af_settings.py` builds an environment's
  runtime from committed defaults, host or CI placement, private secret files, and secrets
  generated once on the host; the runtime file carries `NAME_FILE` paths, never secret
  values, and every check names settings without printing values.
- `[[images]]` entries in `deploy/release.toml` take an optional `target`, a Dockerfile
  build stage, so several images can come from one Dockerfile (for example an
  application image and a backup image with extra tools).
- Deployment notifications and a health monitor for the deployment standard:
  `af_notify.py` sends each recorded engine result (with its reason) and each refused
  release to Telegram, Slack, Discord, or a generic webhook configured per host
  (`notify` in the host config); `af_monitor.py`, run by the `af-monitor` systemd timer,
  notifies once when a running release stops reporting its commit and again when it
  recovers. Delivery is best effort and never changes a deployment.
- Proxy routes for the deployment standard: on hosts with a `[proxy]` table the engine
  writes each project environment's Caddy site file from its `[[routes]]` after the health
  check, reloads Caddy through its admin socket, and puts the old file back when Caddy
  refuses it or the old release is restored. Domains and ports are validated, so placement
  values cannot inject proxy configuration. Host Caddyfile template in
  `assets/deploy/caddy/`; host setup step in the deployment contracts (§11).
- `release-delivery` CI (contracts v1, draft): `af_publish.py` builds every image once
  (multi-architecture, revision label), packs a reproducible deploy bundle, signs every
  digest with the project key (cosign 3, no public log), and moves `release-staging`;
  `promote` turns the verified staging record into the production record without
  rebuilding; `rollback` re-points an environment. GitHub reusable workflow `release.yml`
  and `release.example.yml` (main publishes, `v*` tags promote, manual rollback), with
  cosign, oras, binfmt, and BuildKit pinned in `PINS.md`. Opt-in sandbox test publishes,
  promotes, and rolls back against a real registry.
- The deploy asset scan guard in `tests/test_ci_templates.py` is a lower bound instead of an
  exact count, so new engine files no longer conflict.
- Release triggers for the deployment standard: `af_trigger.py poll`, a pull agent run by
  the `af-agent` systemd timer, and `af_trigger.py receive`, a forced-command SSH receiver
  that accepts only `deploy <environment> [<actor>]` or `status <environment>`. Both run
  the same engine. GitHub template `workflows/push-trigger.yml`, optional push jobs in
  `release.example.yml`, and host setup steps in the deployment contracts (§11).
- Deployment contracts: a service that joins another project's network reaches its own
  services through project-unique aliases, because Docker may resolve a name both
  networks carry to the other project's container (a production migration connected to
  the gateway's `db`); rehearsals use a stand-in network with the real peer names.

### Changed

- Workflow: plans and specs are approved in the session and kept on the local
  implementation branch; one merge request carries specs and code together once the
  owner accepts the result (`default:openspec`, `default:mr-policy`, `change-planning`).
  No merge requests for planning documents alone or for separate archives.
- Skills, the personal profile, and the working-standard description follow the
  one-merge-request workflow (client repositories: push and report the compare link once
  the owner accepts the work).
- `release-delivery` and the deployment contracts state ADR 0010: builders and triggers
  are each project's choice, checks run separately from releases, and nothing billed is
  used without the project's approval.
- `instruction-maintenance`: agent-host memory is never the only home of a rule; owner
  decisions that outlive a session go into the framework in the same piece of work.
- `backend-engineering`: release units share code freely inside themselves and depend on
  each other only through published, versioned contracts (an API or a package version);
  SDK or own thin client is the project's choice.
- Deployment contracts section 12, "Reaching other projects": same-host shared network,
  private network, or public URL, chosen per project; the unique-alias rule applies to
  shared networks.
- Kernel: deploy credentials are limited to the projects they act on (one per device for
  the owner's devices, one per project for shared builders); `AGENTS.md` is updated in
  the same change that alters what it states. ADR 0010 amended: one key per device,
  SSH signatures for direct delivery. Kernel kept at 150 lines by merging related
  bullets; no rule weakened.
- The framework is renamed Pakem. The command is `tools/pakem`, with `tools/pk` as an
  identical short alias; the former `af` entry point and command are removed, and every
  documented `af <command>` is now `pakem <command>`. The library is `tools/pakem_lib`.
- Skill `af-update` is now `pakem-update` (`/pakem-update` in Claude Code); `pakem install`
  prunes an installed `af-update` copy like any skill whose source is gone.
- Installed state uses the new name: `~/.local/state/pakem` (manifest, backups, reports),
  `~/.local/share/pakem/guard.py`, launchd job `com.pakem.drift-report`,
  `~/.codex/rules/pakem.rules`, and the Antigravity hook group `pakem-guard`.
- A device installed under the former name is migrated automatically by the first
  `pakem install --apply` (and by `uninstall`, `enforce`, or `schedule` with `--apply`):
  the state and shared-data directories are moved, an unedited guard is refreshed, the
  guard hooks recorded in Claude Code and Antigravity settings point at the moved guard
  (each file is backed up first), the recorded Codex rules file is moved, and the recorded
  launchd job is replaced. Edited or unrecorded files are reported and left alone, a dry
  run prints the plan, a second run changes nothing, and `pakem doctor` warns until done.
- `pakem report` reads `~/.config/pakem/report.toml`; a config still at
  `~/.config/agent-framework/report.toml` is read and reported, never moved.
- The installed kernel header reads `pakem kernel`, and pinned blocks are written as
  `<!-- pakem:profile ... -->`. Blocks with the former `agent-framework:` marker are still
  read (also by `tools/check_pinned_blocks.sh`); `pakem project check` warns and
  `pakem project sync --profile <dir>` rewrites them.
- Protected branches use the git config key `pakem.protectedBranch`. The pre-push hook and
  the guard fall back to `agentframework.protectedBranch`; `pakem hooks install` copies it
  to the new key and keeps the former key equal for hooks copied before the rename.
- The release engine keeps its runtime identifiers for now (`af_*.py`, the `af-agent` and
  `af-monitor` units, `/etc/af`, `/opt/af`, the deploy user `af`, `AF_*` variables,
  `af.release/v1`, `application/vnd.af.*`, `deploy/af/`, `af-release.yml`, `af-push.yml`);
  they are renamed later with their own migration.
- Promotion waits for the commit's release to be published (`af_publish.py promote
  --wait-minutes`, `publish_wait_minutes` input of the GitHub release workflow, default 30)
  instead of failing when a tag is pushed while the merge's publish is still running; only a
  missing record is retried, every other registry error fails at once.
- Draft contracts v1: `health` and `[[routes]]` name a service and its container `port`
  instead of a placement port setting; the engine asks Docker which `127.0.0.1` port
  publishes it (a binding beyond loopback fails the health check). A route is
  `host_setting`, `service`, `port`.

### Removed

- The `archive/` folder of legacy sources is removed from the tree (it held local paths
  from the original setup); its content stays in the history before 2.0.0. The personal
  profile no longer names a specific commit identity.

### Fixed

- `af_bootstrap.sh` reloads systemd only when a unit file it manages changed, and on GPU
  hosts warns that a reload strips GPU access from containers on the legacy NVIDIA device
  request. The host setup (contracts §11) asks GPU containers to use `runtime: nvidia`.
  A rerun had reloaded systemd every time, and one reload cut a GPU host's inference
  containers off from the GPU.
- Deploy bundles leave out Python bytecode caches (`__pycache__`, `*.pyc`): publishing
  imports `deploy/af/contracts.py` from the checkout, so CI bundles carried a generated,
  timestamped file.
- The release engine runs the one-off migration with its Compose dependencies (the
  database) instead of `--no-deps`, so a project's first deployment can migrate; the
  configuration preflight still runs without dependencies.
- Compose file secrets keep the host file's owner and mode, so containers running as a
  non-root user could not read them on Linux hosts. Secret files are now 0640 (the deploy
  user and its group), the engine exports `AF_SECRETS_GID`, and services that read secrets
  declare `group_add: ["${AF_SECRETS_GID}"]`; existing 0600 secrets are opened up to the
  group on the next deploy.

## [1.3.0] - 2026-10-13

### Changed

- `ui-ux-quality` starts from what the interface is for (ADR 0008): `surfaces.md` gives a
  method plus worked examples for a data dashboard, a public website, and a web
  application (goal, questions, principles, checks). Visual style is a project decision.

### Removed

- The owner design base (`design-base.md`) and the `owner-studio`, `owner-dashboard`, and
  `owner-workbench` token sets; the universal rules (truthful content, accessibility,
  both themes, no colour-only meaning, phone layouts) moved into the skill.

## [1.2.0] - 2026-10-13

### Changed

- Lighter process (ADR 0007): stable base with batched releases, compliance evals
  optional instead of a release gate, ADRs only for real trade-offs, and MAJOR only for
  floor changes or breaking installs.

### Removed

- The kernel digest is no longer pinned in repositories (ADR 0007): `af project sync`
  removes the old block and keeps profile blocks; `af project check` reports a leftover
  block; `kernel/DIGEST.md` is gone. The kernel reaches projects through `af install`.

## [1.1.1] - 2026-10-13

### Changed

- The circuit-breaker eval grades the kernel rule as written: a third change passes
  when the output shows the re-established failure or a new experiment and the
  fixture tests pass (owner decision 2026-10-13: diagnose, then fix). New grader
  kind `unchanged_or_tests_pass`.

### Removed

- The two-failed-fixes line is no longer pinned in the digest (added in 1.1.0): it
  did not change Codex's behaviour, and the shared pinned block stays compact. The
  rule remains in the kernel.

## [1.1.0] - 2026-10-13

### Added

- `af eval run` records each host's configured model in the results (`- Model:`), read
  from the model key of the host's settings, so runs on different models are not
  mixed up.

### Changed

- `hosts/targets.toml`: Antigravity entry re-verified on agy 1.3.3 (24,000-byte file cap
  unchanged; 20,000-token shared rules budget noted; live load check passed).
- The pinned digest now includes the kernel's two-failed-fixes circuit breaker
  (owner decision 2026-10-13), because Codex failed the circuit-breaker eval in
  0.2.0 and 1.0.0 with the rule only in the global kernel.

### Fixed

- `af enforce install` removes the entries an earlier install added that the current
  fragment no longer contains (for example the main-push denies dropped in 1.0.0),
  instead of keeping them until an uninstall.

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
