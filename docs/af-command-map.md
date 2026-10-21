# `af` command map

Status: accepted design (ADR 0011), not yet built. Every area starts as `draft`; the kernel rule "use `af` for
covered operations" applies to an area only when it is `stable`.

Each command lists what it does automatically, the gaps it leaves for the project, the
owner actions it may report, and what `af project check` verifies afterwards.

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
| Output | Short status lines; `--json` gives `{status, done[], owner_actions[], gaps[], checks[]}` |
| Location facts | Host addresses and device paths live in device-local `~/.config/af/`, never in repositories |

## Areas

### 0. Framework status and updates: `af status`, `af update`

- `af status [--brief] [--json]`:
  - the installed framework against merged `main` (network at most every few hours,
    cached);
  - the project's pinned version against the installed one;
  - the project's readiness per stage, with open, due, and provisional decisions.
- `af update`: installs the framework from merged `main` only (`af install`'s provenance
  checks), then lists the kernel and skill files that changed.
- `af config set update notify|apply`: `notify` (default) only tells the owner; `apply`
  is the owner's standing approval for agents to update.
- Hooks (`af enforce`):
  - a session-start hook prints `af status --brief` into each session;
  - a per-message hook compares a local install stamp, without network, and names the
    files to re-read after a mid-session install.
- Hosts without hooks: a kernel line asks agents to run `af status` at session start.

### 1. Device: `af setup device`

- **Automatic:**
  - detects the OS and CPU;
  - installs what is missing after one confirmation: git, the forge CLIs (`gh`, `glab`),
    uv, and a free container runtime (Colima on macOS, Docker Engine on Linux);
  - creates the machine's deploy key, a dedicated SSH key that also signs releases (never
    the login key);
  - writes `~/.config/af/`;
  - installs the framework for agent hosts (`af install`).
- **Owner actions:** forge logins (`gh auth login`, `glab auth login`), VPN sign-in if a
  host needs it.
- **Gaps:** none.
- **Check:** `af setup check` lists tools, versions, the key, and forge logins.

### 2. Host: `af setup host <ssh-target>`

- **Automatic, over SSH from the device:**
  - installs Docker, the release engine and the deploy user (the current
    `af_bootstrap.sh`);
  - installs the notifier and monitor;
  - optionally installs the reverse proxy and the CI runner (rootless Docker, its own
    user, resource caps);
  - registers this device's key.
- Related commands:
  - `af host allow-device <host> <device-key>` and `revoke-device`;
  - `af host add-project <host> <project> <env>`: base, host config, placement prompts;
  - `af host runner add <host> <repo>`: registers a CI runner for one private repository.
- **Owner actions:**
  - the first SSH access to a new server (its password, once);
  - forge runner registration if a token cannot be fetched with the owner's CLI login.
- **Gaps (per host):**
  - notification channels;
  - whether it has a public route (domain and TLS) or is private;
  - runner yes or no;
  - the deploy root.
- **Check:** `af setup check --host <host>` (the current bootstrap `check`, plus keys,
  runner isolation, and each project's base).

### 3. Project: `af project init` / `af project adopt`

- **Automatic:** asks the project's fixed facts, then writes the standard files:
  - **Forge:** GitHub or GitLab; public or private.
  - **Release kind:** `service`, `artifact` or `none`.
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
  - `--adopt` reads an existing project, keeps what already meets the standard, and
    reports only differences and gaps.
- **Owner actions:**
  - creating the repository if the CLI login cannot;
  - protecting tags;
  - setting forge variables that hold secrets.
- **Gaps (examples, never answered by `af`):**
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
- **Decisions:** `af decide <gap> <answer>` records an answer; `--provisional
  <assumption> --revisit <trigger>` or `--pending <what> --from <who> --ask-again <date>`
  record the other two states, in the committed `.af/decisions.toml`.
- **Check:** `af project check` (contracts, CI file, deploy files, `AGENTS.md` blocks),
  and `af project check --gaps`.

### 4. Secrets: `af secret`

- `af secret set <project> <env> <NAME>`: hidden input, or `--from-file`, or
  `--generate <kind>`. Stored in the host's secret store or the forge's protected
  variables, depending on where the project needs it.
- `af secret list <project> <env>`: names and ages only.
- **Owner actions:** typing values only the owner knows.
- **Check:** every secret named in `deploy/settings.toml` exists.

### 5. Release: `af release`

- **Commands:**
  - `af release publish`: the current `main` commit to staging. Refused unless its
    required checks passed. Says so when the project has no staging.
  - `af release promote vX.Y.Z --production`: tags the commit and deploys the same
    release to production.
  - `af release rollback previous|<tag or commit> [--production] [--allow-schema]`:
    "previous" is the last successful release first deployed before the current one;
    each run steps one further back.
  - `af release history [env]`, `af release status [env]`.
- **Delivery by kind:**
  - `service`: a signed `git archive` of the exact commit goes over the device key's
    forced SSH command; the host verifies, builds natively once, and deploys through the
    engine (lock, preflight, backup, migrate, switch, health, restore, record, notify).
  - `artifact`: builds each declared OS and CPU target, signs, and publishes a forge
    release; `rollback` marks a version withdrawn.
  - `none`: tag only.
- **Gaps:** none at release time; all are settled at init. A release refuses while
  required gaps are open.
- **Check:** the deployment record, the health response reports the commit, and the
  notice was sent.

### 6. Framework and agents (existing commands, regrouped)

`af install | uninstall | doctor | lint | changelog | project sync | project check |
project report | schedule | hooks | enforce | eval`. These are unchanged, documented in
the same skill.

## Gap protocol (for coding agents)

A gap is a project- or case-specific decision an `af` command leaves open. `af project
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

1. Run `af project check --gaps --json`. The list is fixed for this session. Do not add
   questions, except a blocker no gap covers; record that as a new gap.
2. Answer what the repository already proves (code, docs, earlier decisions in
   `AGENTS.md`) without asking. Say which gaps were answered this way.
3. Ask the rest in **one message**, grouped by topic, each with its default. "Use the
   defaults" is a complete answer.
4. Write each answer to its `target`, with a one-line reason where the target is
   `AGENTS.md` or a decision note.
5. Run `af project check --gaps`. Ask a follow-up only for answers that failed validation or
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

1. `af setup device`, then `af setup host` (folding in `af_bootstrap.sh`), then
   `af release` for services, then `af project init` and `af project adopt` for GitHub
   and services, with decisions. `af status` and `af update` come first.
2. **Proof 1:** adopt llm-gateway and personal-agent with `af project adopt` and
   release them with `af release`, using only `af` and the gap protocol.
3. GitLab and the `artifact` kind.
4. **Proof 2:** portfolio and cash-flow-tracker.
5. Each area becomes `stable` after its proof; then the kernel rule applies to it.
