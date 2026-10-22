# `pakem` command map

Status: accepted design (ADR 0011), not yet built. Every area starts as `draft`; the kernel rule "use `pakem` for
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
- **`pakem setup host <ssh-target> [--name] [--port] [--trigger pull|push]
  [--deploy-root] [--no-proxy] [--runner] [--plan] [--yes]`:**
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
- **Moved (owner decision 2026-10-22):** `pakem host allow-device` and `revoke-device` go
  to area 5 with the release receiver the device key's forced command calls;
  `pakem host add-project` and per-project notification channels go to `pakem project
  init` and the first release (areas 3 and 5), which read the project's own files.

### 3. Project: `pakem project init` / `pakem project adopt`

- **Automatic:** asks the project's fixed facts, then writes the standard files:
  - **Forge:** GitHub or GitLab; public or private.
  - **Release kind:** `service`, `artifact` or `none`.
  - **Delivery:** `us`, `others` (who releases it, and where our work ends), or `nobody`,
    written to `AGENTS.md`. With `others`, CI and the release steps are the team's;
    `pakem project init` writes no deploy files and work stops at the merge request.
  - **Environments:** production only, or staging too.
  - **Builder:** the host builds from signed source; a GitLab runner; or GitHub-hosted
    runners on public repositories.
  - **Capabilities:** database, health endpoint, scheduled jobs, the AI gateway,
    personal data.
  - **CI workflow** for the forge and runner: push, pull request and tags; superseded
    runs cancelled.
  - **Deploy files:** `deploy/release.toml`, `deploy/settings.toml`,
    `deploy/env/<env>.env` and hook stubs.
  - **Agent files:** `AGENTS.md` (framework block plus project sections),
    `PROJECT_STATE.md`, OpenSpec, and the formatter, linter and test scaffolding for the
    language.
  - `pakem project adopt` reads an existing project, keeps what already meets the standard, and
    reports only differences and gaps.
- **Owner actions:**
  - creating the repository if the CLI login cannot;
  - protecting tags;
  - setting forge variables that hold secrets.
- **Gaps (examples, never answered by `pakem`):**
  - what the health endpoint must prove;
  - which settings are placement or secret;
  - backup and restore commands;
  - migration rules;
  - shared networks and service names;
  - single-instance constraints;
  - data moves;
  - language of user-facing text;
  - gateway profiles and budgets.
- **AGENTS.md:** written once the gaps are settled; later changes update it in the same
  change.
- **Decisions:** `pakem decide <gap> <answer>` records an answer; `--provisional
  <assumption> --revisit <trigger>` or `--pending <what> --from <who> --ask-again <date>`
  record the other two states, in the committed `.pakem/decisions.toml`.
- **Check:** `pakem project check` (contracts, CI file, deploy files, `AGENTS.md` blocks),
  and `pakem project check --gaps`.

### 4. Secrets: `pakem secret`

- `pakem secret set <project> <env> <NAME>`: hidden input, or `--from-file`, or
  `--generate <kind>`. Stored in the host's secret store or the forge's protected
  variables, depending on where the project needs it.
- `pakem secret list <project> <env>`: names and ages only.
- **Owner actions:** typing values only the owner knows.
- **Check:** every secret named in `deploy/settings.toml` exists.

### 5. Release: `pakem release`

- **Commands:**
  - `pakem release publish`: the current `main` commit to staging. Refused unless its
    required checks passed. Says so when the project has no staging.
  - `pakem release promote vX.Y.Z --production`: tags the commit and deploys the same
    release to production.
  - `pakem release rollback previous|<tag or commit> [--production] [--allow-schema]`:
    "previous" is the last successful release first deployed before the current one;
    each run steps one further back.
  - `pakem release history [env]`, `pakem release status [env]`.
- **Delivery by kind:**
  - `service`: a signed `git archive` of the exact commit goes over the device key's
    forced SSH command; the host verifies, builds natively once, and deploys through the
    engine (lock, preflight, backup, migrate, switch, health, restore, record, notify).
  - `artifact`: builds each declared OS and CPU target, signs, and publishes a forge
    release; `rollback` marks a version withdrawn.
  - `none`: tag only.
- **Gaps:** none at release time; all are settled at init. A release refuses while
  required gaps are open, and refuses a project whose `Delivery:` is `others` or `nobody`,
  naming who delivers it.
- **Check:** the deployment record, the health response reports the commit, and the
  notice was sent.

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
