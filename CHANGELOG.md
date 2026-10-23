# Changelog

All notable changes to this framework are recorded here. Versions follow semantic
versioning as defined in `AGENTS.md`.

## [Unreleased]

## [2.1.0] - 2026-10-23

### Added

- `pakem host add <name>` records a server's options on this device only, so an agent can
  interview the owner and record the answers; the owner's step becomes `pakem setup host
  <name>`, which reuses everything recorded (flags only change it). `pakem setup host` with
  no host asks the questions on a terminal; run by an agent, it records what it was given
  and names the short command. Owner actions from `release`, `project init`, and
  `project adopt` use that form. Fixed: a re-run without flags no longer resets recorded
  options to defaults, and `--name` always picks the host it names.
- ADR 0011 (Accepted): `pakem` is a standard core with adaptive edges; agents use it for
  covered operations once an area is stable and fill project-specific gaps with the
  developer through a gap protocol with a firm finish (answered, provisional, or pending
  decisions); framework update awareness; the `pakem` tool's release pace.
- `docs/pakem-command-map.md`: the commands (status and updates, device, host, project,
  secrets, release, decisions), their conventions, gaps, owner actions and checks.
- `pakem propose new | revise | list | show | reject`: the agent that discussed a case
  with the owner writes a proposal, with the exact change drafted against the framework's
  `main`, as a pull request labelled `proposal` (lint and unit tests pass on its branch
  first); the owner's merge accepts it, `revise` answers a change request with a new
  commit, and `reject` records the reason and closes it. ADR 0007 is amended: each
  accepted proposal applies when merged and devices run `pakem update`.
- The `pakem` skill tells every agent how to use the commands: conventions and exit codes,
  each area's stage, the interview-then-short-command flow for servers, projects and the
  gap protocol, releases, and proposals. The areas Proof 1 proved (status and updates,
  device, host, release for services) are `stable`, so agents use `pakem` for them and
  never hand-run the steps (ADR 0011, decisions 4 and 10); project and proposals stay
  `experimental`. The kernel's skills table gains the `pakem` row (its intro line is one
  line shorter, so the kernel stays at 150 lines).
- Client and other private names live in a device-local list,
  `~/.config/pakem/private-names.txt`, never in the public tree. `pakem lint` fails on
  them in the framework, `pakem propose` refuses them, and `pakem project check` warns,
  in a public repository, about each tracked line that names one or a host recorded in
  `hosts.toml` (values never printed). The personal workspace map uses generic wording
  for client folders. The `pakem` skill tells agents to write "the server" and "the
  release port" in public repositories.
- `pakem project init` starts a GitHub project from its fixed facts (visibility, release
  kind, delivery, environments, stack, capabilities): `AGENTS.md`, `.pakem/project.toml`,
  CI for the forge and runner, deploy files and starters for a service we release,
  OpenSpec, `research/`, the stack's tooling and dev tools, the git hooks, the bootstrap
  commit, and a private (or `--public`) GitHub repository with `main` pushed.
- `pakem project adopt` brings an existing repository to the standard on
  `chore/adopt-pakem`: it writes only missing files and lists every difference as a
  finding.
- `pakem decide` records a gap as answered, provisional, or pending in
  `.pakem/decisions.toml` and regenerates the Open decisions list in `AGENTS.md`;
  `pakem project check --gaps` lists each gap with its state and whether its target files
  prove it; `pakem status` raises due decisions; `pakem release` waits for decisions that
  block a release. `pakem project check` warns about starter stubs.
- `pakem release publish | promote | rollback | history | status` release a service by
  direct delivery (ADR 0010): the device signs a request for the exact commit with its
  device key and sends it, with the commit's `git archive` when the host keeps no build
  of it, to the host's new receiver (`af_receive.py`, the device key's forced command).
  The host verifies the signature, builds the images once per commit in a resource-capped
  BuildKit container, and the engine deploys them; promotion and rollback reuse kept
  builds. Publishing refuses a commit whose GitHub checks did not pass, production needs
  `--production`, and `promote` tags the commit after it deploys.
- `pakem host allow-device | revoke-device` let a device's key release a project on a
  host, from the owner's terminal: the receiver writes the project's direct host configs
  and deploy bases from its own deploy files, a forced-command line, and an
  `allowed_signers` line, and names missing placement and secret settings.
- The engine takes direct environments (`delivery = "direct"`) beside registry ones; the
  pull agent skips them, and the bootstrap installs the receiver and checks buildx.
- `pakem setup host --release-port PORT` gives releases their own OpenSSH port, only for the
  deploy user and keys, for hosts whose port 22 is answered by Tailscale SSH (which never
  reads `authorized_keys`, so the device key's forced command did not apply and the key got
  a shell). `setup host` now checks with the device key that the receiver answers and names
  the fix when a shell does; status and history errors keep the host's message (found on
  the owner's server, Proof 1).
- `af_runner.sh` (installed in `/opt/af` by the bootstrap): a free, isolated self-hosted
  GitHub Actions runner for private repositories. `install` creates `ci-runner` with its
  own rootless Docker (from the host Docker's own source), caps its slice, and pins the
  runner by checksum; `add-repo` and `remove-repo` take tokens on standard input only;
  `check` proves the isolation. Opt-in sandbox test: `AF_SANDBOX_RUNNER=1`.
- The GitHub release template takes `multi_architecture` (default true): false skips the
  arm64 emulation and BuildKit container, so host-architecture releases build on a rootless
  self-hosted runner.
- Job containers on the runner reach the host's loopback at `10.0.2.2`, so a job can test a
  container against a server it started; nothing beyond what the job's own steps reach.
- Pakem commands prepare what they need on this device (`git`, `gh` or `glab`, uv, a
  container runtime, the device key, forge logins, the framework) as part of their own
  plan, approved by the owner's instruction in the session; owner-only steps (browser
  logins, sudo without a terminal, the key's passphrase) are reported with the exact
  instruction (exit `3`). Homebrew on macOS, apt on Debian, Ubuntu, and WSL; native
  Windows is refused with a pointer to WSL.
- `pakem setup device`: prepares everything at once for a fresh machine. `pakem setup
  check`: lists every need without changing anything.
- `pakem setup key [--no-passphrase]`: creates the device key `~/.ssh/pakem_device_ed25519`
  with a passphrase (asked by ssh-keygen on the owner's terminal) and adds it to
  ssh-agent, with the macOS Keychain where Apple's `ssh-add` is used.
- `pakem setup host <ssh-target>`: uploads the deploy assets (merged `main` or a release
  only) over one SSH connection, runs the bootstrap with sudo, optionally the CI runner
  (`--runner`), removes the upload, and records the host in `~/.config/pakem/hosts.toml`.
  It signs in with the owner's own login and runs only on the owner's terminal;
  without one it reports the exact command as an owner action.
- `pakem setup check --host <name>`: the bootstrap's check (and the runner's) with the
  recorded options.
- `pakem host runner add|remove <host> <owner/repo>`: registers a repository with the
  host's runner, with a token from the owner's `gh` login that is never printed or
  passed as an argument.
- `pakem enforce install --host codex` also merges `~/.codex/hooks.json`: `SessionStart`
  prints the brief status and `UserPromptSubmit` names the files to re-read after a
  mid-session install. Codex runs them once the owner trusts them in `/hooks`.
- `pakem enforce install --host antigravity` adds the `pakem-status` hook (`SessionStart`
  and `PreInvocation`), answering with `injectSteps`.
- `pakem update` refreshes Codex's rules and hooks together.
- `pakem status`: whether this device runs the latest merged Pakem (a `main` checkout
  follows `origin/main`, a release checkout the newest release on `main`; network at
  most every 4 hours), and whether the project's profile pins match the installed ones.
- `pakem update`: moves the framework checkout to merged `main` or the newest release,
  re-runs the recorded `pakem install --apply` and `pakem enforce install`, and lists the
  files agents must re-read. Refuses unmerged or dirty checkouts.
- `pakem config set update notify|apply`: whether agents may run `pakem update`
  themselves (default `notify`).
- `pakem enforce install --host claude` adds a session-start hook (brief status) and a
  per-message hook (files to re-read after a mid-session install).
- `pakem install` records the installed commit and its options in the manifest, for
  `pakem status` and `pakem update`. Exit codes follow the command map (`3` owner action,
  `6` refused, `75` busy).
- `pakem help` lists each area's maturity stage.

### Changed

- The pre-push hook lets a new repository's first push create `main` with its single root
  commit `chore: bootstrap project`; every other push to `main` keeps the
  documentation-only rule. The kernel's branching line and the project-bootstrap skill say
  so (owner decision 2026-10-22).
- Kernel: every fix or feature branches from `main` and its merge request targets `main`,
  never another topic branch (no stacked merge requests) unless the project's
  `default:branching` override says otherwise.
- CI: a fast path for docs-only pull requests (a job-level `if:`, never `paths-ignore`),
  and caches kept local on self-hosted runners (proposal #65).
- `pakem project init` and `adopt` write CI for `Delivery: others` too, as for any
  project (owner decision, 2026-10-23). A client that runs its own CI opts out with
  `--client-ci WHERE`, which writes no CI and records gap `ci.owner`; the gap stays open
  until the Pakem `ci.yml` exists or the decision names the client's CI. Tag protection is
  asked only for projects we release, and no longer when GitHub could not be asked.
- Kernel section 6: a framework proposal (`pakem propose`) opens its pull request before
  acceptance, carrying the change itself; the owner's merge accepts it (owner decision,
  2026-10-23). Section 8: `pakem` is mandatory, never by hand, only where an area is
  stable, as ADR 0011 decision 4 says.
- Release policy (owner decision, 2026-10-23): the `pakem` tool releases whenever an
  area is proven; kernel and skill text never triggers a release alone and waits for the
  next one, batched at most once a month. ADR 0007, ADR 0011 decision 9, `AGENTS.md`, and
  `instruction-maintenance` now say the same.
- `pakem setup host <target> --project-dir DIR` is the one owner step before releasing: it
  makes the device key if missing, installs or updates the engine, and lets the key release
  each project (switching registry environments to direct delivery), over one connection.
  The owner actions of `pakem release`, `project init`, and `project adopt` name that one
  command; `pakem setup key` and `pakem host allow-device` stay for single steps later.

### Fixed

- `pakem project adopt` no longer adds starter code (`src/`, smoke tests) or unused CI
  templates to an existing project with its own `ci.yml`, and reports what the registry
  mode left behind when the project moves to direct delivery. Its owner actions skip a
  runner GitHub already lists, a device already allowed, and tag protection where it
  needs a paid plan (found adopting llm-gateway and personal-agent, Proof 1).
- `pakem project adopt` asks to remove only the registry publisher
  (`deploy/af/af_publish.py`) and signing key, not `deploy/af/contracts.py`, which a
  project's own checks may still run (found adopting personal-agent, Proof 1).
- `pakem decide` and `pakem project adopt` refuse, changing nothing, unless the Open
  decisions markers in `AGENTS.md` appear once each, start before end, on their own lines.
  A marker quoted in prose, or a missing end marker, used to delete the owner's text.
- Pakem writes TOML strings with a real escaper: an emoji or other non-BMP character in
  an answer, a reason, a delivery note, a host path, or a project description no longer
  breaks `.pakem/decisions.toml` (and with it `decide`, `check --gaps`, `status`, and
  `release`). Adopt keeps `.pakem/project.toml` when its facts are unchanged, and keeps
  keys it does not know.
- Gap checks no longer pass on the starter files alone. `health.proof` needs its
  `[health] path` served by the project's own code (not a starter stub) and a real
  `docs/deployment.md`. `settings.sources` fails while `deploy/settings.toml` is still the
  template. `migrations.rule` and `gateway.profile` gain checks (`AGENTS.md` states the
  rule, or names the gateway with a `default:no-billed` approval). A bulleted
  `- Language:` line now counts. The gap protocol tells agents to read the code an answer
  depends on, such as the health handler.
- CI written by `pakem project init` for a private repository now runs: it passed the
  self-hosted runner as the one string `[self-hosted, linux, x64, pakem]`, which no runner
  matches, so jobs waited forever; it passes the single label `pakem` now. The generated
  `database` job passes the template's required `test_cmd` and a `migrate_cmd` stub that
  fails until the project sets it (found linting generated CI with actionlint).
- `pakem setup host --release-port` refuses a port another program already listens on
  (naming it), requires that only sshd listens on the release port on every address,
  drops an earlier release port when the port changes, and puts the old sshd settings back
  when sshd cannot take the port. On the owner's server a Git service held 2222 on IPv4,
  so releases reached it instead of sshd.
- A release port can no longer lock the owner out: `pakem setup host` and `pakem host add`
  refuse a release port equal to the port the owner logs in on (`--port`, or what
  `ssh -G` gives the target), and `af_bootstrap.sh --release-port` refuses any port sshd
  already serves, other than the earlier release port. With `AllowUsers af` on such a
  port, only the deploy user could log in there, and recovery needed the console.
- The release port must be held by this host's own ssh service (its main process, or
  systemd when sshd is socket-activated): a second sshd on that port, such as a Git
  server's or a container's, is refused by pid, because it has its own settings and never
  the deploy user's forced command (found on the owner's server, where another sshd held
  2222 on the Tailscale address).
- `pakem release rollback previous` returns the last release that was live and working
  before the running one, and never one a rollback left: after A, B, a rollback to A, and
  C, it returns A, not B. A host's receiver now records rollbacks with trigger `rollback`.
- The receiver refuses a publish to production and a promotion to any other environment,
  whatever the client checked.
- An image's `context` and `dockerfile` in `deploy/release.toml` must be relative paths
  inside the repository; the receiver also refuses either when it resolves outside the
  commit's tree. An absolute context could bake another project's secrets into an image.
- `pakem release status | history | rollback` see the release an environment runs however it
  was delivered: a registry release from before the move to direct delivery showed as
  "running nothing", and `rollback previous` could not step back from it. The host reports
  it from its `current` release (after `pakem setup host` updates the receiver); until
  then the device reads it from the deployment record.
- `pakem status` shows the short installed and target commits beside the versions, so
  an update between releases no longer reads "2.0.1 -> 2.0.1".
- After a proposal is merged, agents tell the owner `pakem update` is due and run it only
  on the owner's instruction or with `update = "apply"`, as ADR 0011 decision 7 says; the
  `pakem` skill, the command map, and lessons intake had said to run it.

### Security

- `pakem propose new` and `revise` refuse (exit 6) a proposal whose text or added lines hold
  a private name, a host, address, or port from `hosts.toml`, a non-documentation IP
  address, or a home directory path, naming each place but never the value. They print the
  text and ask before pushing (`--yes` skips the question, after the owner saw it). The
  project is "a project" unless `--project` names it. Subjects are `docs:` (`feat:` for
  tooling, or `--type`), cut at a word within 72 characters.
- The release receiver no longer writes an archive entry through a symlink extracted
  earlier, refuses an entry given twice, and refuses any symlink that resolves outside the
  source tree once all are in place. Chained links (`p/q -> ..`, `r -> p/q/..`) could
  write anywhere the deploy user can. Run `pakem setup host <name>` to update each host.
- The self-hosted CI runner serves private repositories only, as GitHub reports them:
  `pakem host runner add` and `af_runner.sh add-repo` refuse a public repository, whose
  pull requests could run any fork's code on the server, and `af_runner.sh check` fails
  for a runner registered for one. `pakem project adopt` reads the visibility from GitHub
  instead of a flag or a prompt, and `pakem project check` exits 6 when a public
  repository has self-hosted jobs.

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
