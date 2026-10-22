# `pakem` command map

Status: accepted design (ADR 0011), being built; each area's stage is in its section and in `pakem help`. Every area starts as `draft`; the kernel rule "use `pakem` for
covered operations" applies to an area only when it is `stable`.

Each command lists what it does automatically, the gaps it leaves for the project, the
owner actions it may report, and what `pakem project check` verifies afterwards.

## Conventions (all commands)

| Convention | Meaning |
|---|---|
| Modes | Interactive by default; `--plan` prints the steps without doing them; `--json` prints machine-readable results; every prompt has a flag, so `--yes` plus flags runs with no prompts |
| Re-runs | Every command is safe to run again: it detects what is done and continues |
| Exit codes | `0` done; `2` usage; `3` owner action required (listed in the output); `4` open gaps; `5` check failed; `6` refused (safety rule); `75` busy (lock) |
| Owner actions | Steps only the owner can do (browser login, first server password, secret values, accounts outside our control) are printed as `OWNER ACTION REQUIRED` with the exact instruction and verified on the next run |
| Production | Any production change needs `--production` and, for agents, the owner's instruction in the session |
| Secrets | Read from hidden input or a file, written straight to their store; never printed, logged, or put in arguments |
| Billing | Only free services and tiers unless the project approves a billed one (`default:no-billed`) |
| Requirements | Each command declares what it needs (tools, runtime, device key, logins) and prepares what is missing in its own plan; the owner's instruction in the session to run it approves the installs it lists (ADR 0011, decision 8) |
| Output | Short status lines; `--json` gives `{status, done[], owner_actions[], gaps[], checks[]}` |
| Location facts | Host addresses and device paths live in device-local `~/.config/pakem/`, never in repositories |

## Areas

### 0. Framework status and updates: `pakem status`, `pakem update`

Stage: `experimental` (built 2026-10-22).

- `pakem status [--brief] [--json] [--refresh | --offline]`:
  - the installed framework against merged `main`: a `main` checkout follows
    `origin/main`, a release checkout (what `install.sh` makes) follows the newest
    release tag on `main`, and any other checkout follows nothing (the check is off and
    `pakem update` refuses);
  - network at most every 4 hours (`git fetch` without prompts, cached in the state
    directory); `--refresh` fetches now, `--offline` never;
  - the project's profile pins in `AGENTS.md` against the installed profiles, and
    `pakem project check`'s error count;
  - read-only apart from the fetch and its cache; always exit `0`.
- `pakem update [--plan] [--yes] [--json]`:
  - fast-forwards a `main` checkout to `origin/main`, or checks out the newest release;
  - re-runs the new checkout's `pakem install --apply` with the hosts and profiles the
    last install recorded, then `pakem enforce install` for every host enforced before;
  - lists the kernel and skill files that changed, to re-read;
  - refuses (`6`) a checkout on another branch, with uncommitted changes, or with commits
    `origin/main` lacks; an install made before options were recorded that used profiles
    needs one `pakem install --apply --profile ...` first (`3`); busy (`75`) while
    another update runs.
- `pakem config set update notify|apply`: `notify` (default) only tells the owner; `apply`
  is the owner's standing approval for agents to update.
- Hooks (`pakem enforce install --host <host>`), on every supported agent host:
  - a session-start hook prints `pakem status --brief` into each session, with advice
    that follows the `update` setting;
  - a per-message hook compares the installed files with the ones the session started
    with, without network, and names that host's files to re-read after a mid-session
    install.

  | Host | Session start | Per message | Output | Notes |
  |---|---|---|---|---|
  | Claude Code | `SessionStart` | `UserPromptSubmit` | plain text | in `~/.claude/settings.json` |
  | Codex | `SessionStart` (startup, resume, clear) | `UserPromptSubmit` | plain text | `~/.codex/hooks.json`; the owner trusts the hooks once in `/hooks` (again after each change) |
  | Antigravity | `SessionStart` | `PreInvocation` (each model call) | `injectSteps` with an `ephemeralMessage` | `~/.gemini/config/hooks.json`, hook `pakem-status` |

- A host that offers no session hook gets a kernel line asking agents to run
  `pakem status` at session start; every supported host has one today.

### 1. Device: `pakem setup device`, `pakem setup key`, `pakem setup check`

Stage: `experimental` (built 2026-10-22; on-demand requirements and key passphrase
2026-10-22).

- **Requirements on demand** (`tools/pakem_lib/requirements.py`): every command names the
  needs it uses from this list and prepares what is missing, with the same plan, one
  confirmation, and owner actions as the rest of the command:

  | Need | Prepared by pakem | Owner action when pakem cannot |
  |---|---|---|
  | `git`, `gh`, `glab`, `uv`, `docker` | Homebrew (macOS), apt (Debian, Ubuntu, WSL) | sudo without a terminal; tools apt lacks (uv); no package manager |
  | `container-runtime` | keeps any runtime that answers `docker info`; installs and starts Colima on macOS | Linux: start Docker Engine and join the `docker` group; WSL: Docker Desktop's WSL integration or Docker Engine |
  | `device-key` | on a terminal, `ssh-keygen` asks for the passphrase | without a terminal: `pakem setup key` |
  | `device-key-unlocked` | macOS: reloads the passphrase from the Keychain; on a terminal, `ssh-add` | `ssh-add ~/.ssh/pakem_device_ed25519`, or start an ssh-agent |
  | `github-login`, `gitlab-login` | none | `gh auth login`, `glab auth login` |
  | `framework` | `pakem install --apply` | none |
  | `device-settings` | writes `~/.config/pakem/device.toml` (name, system, WSL, CPU, key path, fingerprint, passphrase) | none |

  For example `pakem project init` for a GitHub project needs `git`, `gh`, and
  `github-login`, never `glab`.
- **Device key** `~/.ssh/pakem_device_ed25519`: a dedicated ed25519 key that connects to
  hosts and signs releases, never the login key; pakem never reads or prints its private
  half. It has a passphrase by default and is used through ssh-agent, the same on every
  system: the macOS Keychain unlocks it without prompts (with Apple's `ssh-add`); Linux
  and WSL need `ssh-add` once per login or boot. `pakem setup key --no-passphrase` is
  only for a device nobody can unlock (a headless machine), recorded in `device.toml`.
- `pakem setup device [--plan] [--yes] [--json]`: prepares every need at once, for a fresh
  machine.
- `pakem setup check [--json]`: lists every need without changing anything; exit `0` all
  present, `3` owner actions remain, `5` something pakem would prepare is missing.
- **Systems:** macOS, Linux, and WSL (detected from the kernel); native Windows is refused
  with a pointer to WSL.
- **Gaps:** none.

### 2. Host: `pakem setup host <ssh-target>`

Stage: `experimental` (built 2026-10-22).

- **Who signs in:** the owner's own admin login with sudo, from the owner's terminal only
  (owner decision 2026-10-22; floor: no personal identity for automation). Without a
  terminal the command stops with `OWNER ACTION REQUIRED` and the exact command line; it
  never connects. `--plan` works anywhere. The device key is never used for
  administration: it may only deploy, through a forced command (area 5).
- **The agent interviews, the owner runs one short command** (owner decision
  2026-10-22): the agent asks in chat for the server's SSH target, whether it is private,
  whether it runs the CI runner, and whether its port 22 is Tailscale SSH (then a free
  release port), and records the answers with **`pakem host add <name> --target T
  [--port] [--trigger] [--deploy-root] [--proxy|--no-proxy] [--runner|--no-runner]
  [--release-port] [--project-dir DIR ...]`**, which writes only `hosts.toml` and never
  connects. The owner then runs **`pakem setup host <name>`**: a recorded host's options
  are reused, and given flags only change them. `pakem setup host` with no host, on a
  terminal, asks the same questions itself; an agent running it without a terminal
  records what it was given and gets the short command as its owner action.
- **`pakem setup host [<name or ssh-target>] [options as above] [--no-passphrase]
  [--plan] [--yes]`:** the one owner step before releasing (owner request 2026-10-22: one
  command, not three):
  - with projects (the host's recorded `--project-dir`s, or the current directory when it
    is a service project), first makes this device's key if it has none (`setup key`'s prompt), and
    after the install lets the key release each project (`host allow-device`, switching an
    environment that deploys from a registry to direct delivery);
  - after allowing the projects, checks with the device key that the release receiver
    answers; when a shell answers instead (Tailscale SSH on port 22 logs in by tailnet
    identity and never reads `authorized_keys`, so forced commands do not apply), it stops
    with exit `5` and names `--release-port`;
  - `--release-port PORT` (hosts like that only; a normal OpenSSH host needs none): the
    bootstrap adds an sshd drop-in, checked with `sshd -t` and undone on any problem,
    where OpenSSH also listens on PORT for the `af` user with keys only; every port sshd
    listened on before stays; releases use that port, recorded in `hosts.toml`;
  - one SSH connection (ControlMaster), so a password is asked at most once;
  - uploads `VERSION` and the deploy assets, in the checkout's layout, to a temporary
    directory, runs `af_bootstrap.sh install` with sudo (Docker, cosign, Caddy unless
    `--no-proxy`, the `af` deploy user, the engine in `/opt/af`, the agent and monitor
    timers), and `af_runner.sh install` with `--runner` (rootless Docker, its own user,
    resource caps); removes the directory whatever happens;
  - uploads only from merged `main` or a release, like `pakem install` (`--allow-unmerged`
    and `--allow-dirty` exist for deliberate tests);
  - records the host in the device-local `~/.config/pakem/hosts.toml` (target, port,
    deploy root, trigger, proxy, runner); exit `5` when the install ran but the host's
    check failed (the host is still recorded).
- **`pakem setup check --host <name>`:** the bootstrap's `check`, with the recorded
  options, plus the runner's isolation check when the host has a runner.
- **`pakem host runner add|remove <host> <owner/repo>`:** needs `gh` and a GitHub login
  (prepared on demand); takes a registration or removal token from the owner's `gh`
  login, keeps it in memory, and hands it to `/opt/af/af_runner.sh` on standard input
  through a file only the login user can read, removed right after. Refused when the
  host has no runner.
- **Owner actions:** running these commands on a terminal; the server's first password
  if SSH asks for one; a GitHub login for runner tokens.
- **Gaps (per host), answered by flags:** public route or private (`--no-proxy`), runner
  (`--runner`), deploy root (`--deploy-root`), trigger (`--trigger`).
- **Moved (owner decision 2026-10-22):** `pakem host allow-device` and `revoke-device` are
  in area 5 with the release receiver the device key's forced command calls;
  `pakem host add-project` and per-project notification channels go to `pakem project
  init` and the first release (areas 3 and 5), which read the project's own files.

### 3. Project: `pakem project init` / `pakem project adopt`

Stage: `experimental` for GitHub (built 2026-10-22); GitLab and the `artifact` kind are
`draft`.

- **Facts** (asked once on a terminal, or flags; recorded in the committed
  `.pakem/project.toml`): `--description`, `--visibility private|public` (`--public`),
  `--kind service|none` (`artifact` comes later), `--delivery us|others|nobody` (with
  `--delivery-note`), `--environments production|staging,production`, `--stack
  python|node`, `--capabilities` (the registry's names, or `none`; a service adds
  `deployed`). Without a terminal a missing fact is a usage error naming its flag.
- **`pakem project init <directory> [--name] [--repository owner/name]`** (owner
  decisions 2026-10-22):
  - writes the standard files: `AGENTS.md` (purpose, `Capabilities:`, `Delivery:`,
    commands, the generated Open decisions list), `.pakem/`, `.gitignore`,
    `.editorconfig`, OpenSpec `config.yaml`, `research/`, `CHANGELOG.md` (kind not
    `none`), CI unless `Delivery: others` (`ci.yml` on push, pull request, and `v*` tags,
    superseded runs cancelled, plus the verify, security, and database templates the
    capabilities switch on; private repositories run on the self-hosted runner, public ones
    on GitHub-hosted runners), the deploy files for a service we release (contracts §4,
    starters marked `pakem:stub`: `compose.yaml`, `Dockerfile`, `deploy/hooks/backup`,
    `docs/deployment.md`), and the stack's tooling (uv with ruff, pyright, pytest; or npm
    with prettier, eslint, TypeScript), with the dev tools added through the stack's
    package manager;
  - installs the git hooks, keeps `PROJECT_STATE.md` and `JOURNAL.md` untracked, commits
    `chore: bootstrap project`, creates the GitHub repository with the owner's `gh` login
    (private unless `--public`) and pushes `main`: the pre-push hook allows a new
    repository's single root bootstrap commit, nothing else.
- **`pakem project adopt [directory]`** (owner decision 2026-10-22): takes what the
  repository shows (origin, stack, `Delivery:`, `Capabilities:`, deploy files), asks the
  rest, switches from `main` to `chore/adopt-pakem`, writes only missing files (and the
  generated Open decisions section of `AGENTS.md`), and lists every difference as a
  finding: CI templates that differ, missing `AGENTS.md` lines, Python tooling defaults
  not merged, `deploy/release.toml` contract problems, starter stubs, and what the
  registry mode left behind (publishing workflows, `deploy/af/af_publish.py`, the cosign key, no
  `[build] runner = "local"`). It never adds starter code, keeps the project's own
  `ci.yml` (the reusable templates come only with a Pakem `ci.yml`), installs nothing,
  and only reads the forge.
- **Owner actions** (skipped when GitHub or this device already shows them done): a
  self-hosted runner registration for a private repository (`pakem host runner add`);
  protecting `v*` tags (not asked where rulesets need a paid plan, as on free private
  repositories); `pakem host allow-device` before a service's first release; creating the
  repository when `gh` cannot.
- **Gaps** (`pakem project check --gaps [--json]`, exit `4` while a required gap is
  open), from the facts:

  | Gap | Required | Blocks | Proven by |
  |---|---|---|---|
  | `language.text` | yes | - | a filled `Language:` line in `AGENTS.md` |
  | `health.proof` | service | release | `deploy/release.toml [health]` |
  | `settings.sources` | service | deploy setup, release | `deploy/settings.toml` names its settings |
  | `network.shared` | no (none) | deploy setup | - |
  | `single-instance` | no (no constraint) | release | - |
  | `backup.restore` | database | release | `deploy/hooks/backup` is the project's own, executable |
  | `migrations.rule` | database | release | - |
  | `data.moves` | no (none), database | release | - |
  | `gateway.profile` | llm | release | - |

- **Decisions:** `pakem decide <gap> <answer> [--reason]`,
  `--provisional <assumption> --revisit <trigger>`, or
  `--pending <what> --from <who> --ask-again <date>`; `--question` and `--blocks` record a
  blocker no gap covers. Written to the committed `.pakem/decisions.toml`; the Open
  decisions list in `AGENTS.md` is regenerated. An answer whose target does not prove it
  stays open with the reason. `pakem status` counts open, pending, and provisional
  decisions and raises due ones (also at session start); `pakem release` refuses while a
  required gap that blocks release is open or pending (exit `4`).
- **Check:** `pakem project check` (contracts, CI file, deploy files, `AGENTS.md` blocks,
  starter stubs), and `pakem project check --gaps`.

### 4. Secrets: `pakem secret`

- `pakem secret set <project> <env> <NAME>`: hidden input, or `--from-file`, or
  `--generate <kind>`. Stored in the host's secret store or the forge's protected
  variables, depending on where the project needs it.
- `pakem secret list <project> <env>`: names and ages only.
- **Owner actions:** typing values only the owner knows.
- **Check:** every secret named in `deploy/settings.toml` exists.

### 5. Release: `pakem release`

Stage: `experimental` for `service` (built 2026-10-22); `artifact` and `none` are `draft`.

- **Commands** (run in the project's repository, or with `--project-dir`; `--plan`,
  `--yes`, `--json`):
  - `pakem release publish`: the current `origin/main` commit to staging. Refused unless
    its checks passed (GitHub check runs and commit statuses: at least one, none failing
    or pending), and when the project declares no staging (it names `promote` instead).
  - `pakem release promote vX.Y.Z --production [--commit REV]`: the commit staging runs
    (or `origin/main` without staging, or `--commit`) to production, with the same images;
    the version must be newer than every release tag. After it deploys, the commit is
    tagged and the tag pushed to `origin`; a failed deployment is never tagged.
  - `pakem release rollback previous|<tag or commit> [--environment E] [--production]
    [--allow-schema]`: "previous" is the release first deployed before the running one,
    so each run steps one further back; failed and restored attempts never count. The
    host refuses a target never deployed there, and one with another schema version
    unless `--allow-schema` (after restoring the database from its backup).
  - `pakem release history [env]`, `pakem release status [env]`: read from the host.
- **Needs** (prepared on demand): git, ssh, the device key unlocked; gh with a GitHub
  login for the checks. GitLab checks come with the GitLab area.
- **Delivery by kind:**
  - `service` (direct delivery, ADR 0010): the device signs a request
    (`pakem.release-request/v1`: action, project, environment, commit, version, archive
    digest, time, device) with `ssh-keygen -Y` and the device key, and sends it as the
    deploy user `af`, whose `authorized_keys` line forces `af_receive.py serve`. The
    commit's `git archive` goes along only when no environment on the host keeps a build
    of it. The host checks the signature against the device's own `allowed_signers` line
    for that project, refuses requests older than ten minutes, builds each image once per
    commit in a BuildKit container capped at 2 CPUs and 4 GB (one build at a time; cache
    pruned after 7 days), and runs the engine (lock, preflight, backup, migrate, switch,
    health, restore, record, notify). Contracts §13.
  - `artifact`: builds each declared OS and CPU target, signs, and publishes a forge
    release; `rollback` marks a version withdrawn.
  - `none`: tag only.
- **`pakem setup host <target> --project-dir DIR`** is how a device is allowed (area 2);
  agents that need it get that one line as their owner action.
- **`pakem host allow-device <host> [--project-dir] [--switch-from-registry]`** (owner's
  admin login, terminal only) does only the last part, for one more project later: it runs
  `af_receive.py authorize` as root,
  which writes a direct host config and deploy base for every environment the project's
  `deploy/release.toml` declares, the device key's forced-command line listing the
  projects it may release, and its `allowed_signers` line listing the projects it may
  sign for; it names placement and secret settings still missing (names only). Records
  the project for that host in `~/.config/pakem/hosts.toml`. An environment that deploys
  from a registry moves only with `--switch-from-registry`.
- **`pakem host revoke-device <host> [--device NAME] [--project P]`:** removes a device's
  lines, or one project from them; a lost device is revoked from another device by name.
- **Owner actions:** `setup host --project-dir` (or `allow-device`) and `revoke-device` on a terminal; placement values
  and secrets on the host until `pakem secret` exists (area 4); pushing the tag if the
  push fails.
- **Gaps:** none at release time; all are settled at init. A release refuses a project
  whose `AGENTS.md` says `Delivery: others` or `nobody`, and while a required gap that
  blocks release is open or pending (area 3).
- **Decided while building (2026-10-22):** staging and production of a project share one
  host (promotion reuses the staging build there); a host keeps build cache for 7 days.
- **Check:** the deployment record, the health response reports the commit, and the
  notice was sent.

### 7. Proposals: `pakem propose`

Stage: `experimental` (built 2026-10-22; owner decisions: the agent writes proposals, each
accepted one is its own pull request and applies when merged).

- **Flow:** the agent discusses a case with the owner in the project where it came up,
  then runs `pakem propose new` there; the owner reviews the issue; on acceptance the
  agent runs `pakem propose accept <n>`; the owner merges; the agent runs `pakem update`.
- **`pakem propose new --title --rule --case --why-general --home
  kernel|skill|reference|template|tooling|adr --change-file DIFF [--evidence ...]
  [--replaces] [--enforcement] [--project] [--plan] [--new-subject]`:** checks the diff
  applies to the framework's `origin/main` (in a temporary worktree), refuses when earlier
  proposals look like the same subject (unless `--new-subject`), and opens an issue
  labelled `proposal` on the framework repository with the owner's `gh` login.
- **`revise <n>`** replaces the text after review; **`list [--state]`** and
  **`show <n>`** read them; **`reject <n> --reason`** comments, labels, and closes.
- **`accept <n>`** (only after the owner accepted): a branch
  `docs/proposal-<n>-<slug>` in a separate worktree beside the framework checkout,
  the drafted change applied, a change fragment, `pakem lint` and the unit tests, push,
  and a pull request that closes the issue; the worktree is removed afterwards, or kept
  for a look when a check failed.
- **Needs:** git, gh, a GitHub login (prepared on demand).

### 6. Framework and agents (existing commands, regrouped)

`pakem install | uninstall | doctor | lint | changelog | project sync | project check |
project report | schedule | hooks | enforce | eval`. These are unchanged, documented in
the same skill.

## Gap protocol (for coding agents)

A gap is a project- or case-specific decision an `pakem` command leaves open. `pakem project
check --gaps --json` lists them:

```json
{"id": "health.proof", "required": true,
 "question": "What must /health prove before a release counts as working?",
 "why": "The engine restores the previous release when health fails.",
 "options": ["process up", "process up and database answers", "custom"],
 "default": "process up and database answers",
 "target": "deploy/release.toml [health] + docs/deployment.md",
 "done_when": "release.toml health block validates and the endpoint exists in code",
 "blocks": ["release"]}
```

The agent fills gaps in this order, and stops when the finish condition holds:

1. Run `pakem project check --gaps --json`. The list is fixed for this session. Do not add
   questions, except a blocker no gap covers; record that as a new gap.
2. Answer what the repository already proves (code, docs, earlier decisions in
   `AGENTS.md`) without asking. Say which gaps were answered this way.
3. Ask the rest in **one message**, grouped by topic, each with its default. "Use the
   defaults" is a complete answer.
4. Write each answer to its `target`, with a one-line reason where the target is
   `AGENTS.md` or a decision note.
5. Run `pakem project check --gaps`. Ask a follow-up only for answers that failed validation or
   contradict each other, in one message.
6. **Finish:** every required gap is answered, provisional (with a revisit trigger), or
   pending (with what is missing, from whom, and a date to ask again), each recorded with
   its reason. After the second round, record anything still unclear as pending; never
   start a third round.

Pending gaps are not asked again until they are due: their date passed, or the work
reached a stage they block; then they come up at session start as one question. Each gap
declares the stages it blocks, so agents work on everything else meanwhile and keep the
pending choice open in the design.

Optional gaps take their default unless the developer objects; they are listed once, not
asked one by one.

## Build order and proof

1. `pakem setup device`, then `pakem setup host` (folding in `af_bootstrap.sh`), then
   `pakem release` for services, then `pakem project init` and `pakem project adopt` for GitHub
   and services, with decisions. `pakem status` and `pakem update` come first.
2. **Proof 1:** adopt llm-gateway and personal-agent with `pakem project adopt` and
   release them with `pakem release`, using only `pakem` and the gap protocol.
3. GitLab and the `artifact` kind.
4. **Proof 2:** portfolio and cash-flow-tracker.
5. Each area becomes `stable` after its proof; then the kernel rule applies to it.
