---
name: pakem
description: Use when setting up a device or server, starting or adopting a project, recording a project decision, releasing, rolling back or checking a service, or proposing a framework change. Pakem's commands cover these; run them instead of doing the steps by hand, and fix pakem when it falls short.
---

# Using pakem

`pakem` runs every operation that is the same in every project; the project decides only
what its gaps ask (ADR 0011). For an area marked `stable` below, use `pakem` and never
hand-run the equivalent steps. If `pakem` lacks something or fails, report it with its
exact output and fix `pakem` (`pakem propose` from a project, or a branch in the framework);
never work around it. `pakem help` shows each area's stage.

## 1. Conventions

- `--plan` shows the steps and changes nothing; `--yes` skips the question; `--json` gives
  `{status, done, owner_actions, gaps, checks}`. Commands are safe to run again.
- Exit codes: `0` done, `2` usage, `3` owner action, `4` open gaps, `5` check failed,
  `6` refused (a safety rule), `75` busy. Read the message; never retry a refusal unchanged.
- `OWNER ACTION REQUIRED:` lines are steps only the owner can do. Pass them on exactly, in
  their short form, and check the result when the owner reports back.
- Commands prepare what they need (tools, logins, the device key) and list the installs;
  the owner's instruction in the session to run the command approves them.
- Production changes need `--production` and the owner's explicit instruction in this
  session. Merging a pull request is not that instruction.

## 2. Areas

| Area | Stage | Commands |
| --- | --- | --- |
| Status and updates | stable | `status`, `update`, `config` |
| Device | stable | `setup device`, `setup key` (owner), `setup check` |
| Host | stable | `host add`, `setup host` (owner), `setup check --host`, `host runner` |
| Release (services) | stable | `release publish`, `promote`, `rollback`, `history`, `status`, `host allow-device`, `host revoke-device` |
| Project | experimental | `project init`, `project adopt`, `project check --gaps`, `decide` |
| Proposals | experimental | `propose new`, `revise`, `list`, `show`, `reject` |

Experimental areas: prefer `pakem` too, but check its result closely and report surprises.

## 3. Status and updates

- At session start a hook runs `pakem status`; act on what it reports. A due project
  decision is asked of the owner once, then recorded with `pakem decide`.
- Run `pakem update` only on the owner's instruction (or when `pakem config get update`
  is `apply`); afterwards re-read the files it names (`pakem-update` skill).

## 4. Servers: the agent interviews, the owner runs one short command

Server administration uses the owner's own login and sudo, from the owner's terminal only;
an agent never connects as the owner. Instead:

1. Ask the owner, in one message: the SSH target (an alias in their `~/.ssh/config` or
   `user@address`); whether the server is private (no public web traffic); whether it
   runs the CI runner for private repositories; and whether its port 22 is answered by
   Tailscale SSH or anything else that skips `authorized_keys` (then a free port, such
   as 2223, for releases).
2. Record the answers: `pakem host add <name> --target <ssh-target> [--no-proxy]
   [--runner] [--release-port <port>] --project-dir <each service project>`. It writes
   only `~/.config/pakem/hosts.toml` and never connects.
3. Give the owner the one line: `pakem setup host <name>`. It reuses everything recorded,
   makes the device key if needed (the owner types its passphrase), installs or updates
   the engine, allows the projects, and checks with the device key that the release
   receiver answers.
4. When it stops, read its message: a refused release port names the program holding it;
   "gave the device key a shell" means the host needs `--release-port`.

## 5. Projects and decisions

- New repository: `pakem project init <dir>` with every fact as a flag (or ask the owner
  first). Existing repository: `pakem project adopt` on its own branch; resolve each
  `FINDING` it prints with the owner, never by guessing.
- Gaps follow [the gap protocol](references/gap-protocol.md): answer what the repository
  proves, ask the rest in one message, record each with `pakem decide`, and stop after two
  rounds.
- In a public repository, committed text (specs, evidence, docs) says "the server" and
  "the release port", never a host name, address, or port; `pakem project check` warns
  when tracked files name a host from `hosts.toml` or a private name.

## 6. Releases (services)

- `pakem release publish` sends `origin/main` to staging; `pakem release promote vX.Y.Z
  --production` deploys to production and tags the commit afterwards; `pakem release
  rollback previous|<tag or commit> [--production]` returns to an earlier release.
- Before a production release: CI passed on the exact commit (`pakem` refuses otherwise),
  the version matches the project's files (a release-prep pull request first), the owner
  said to release in this session, and `--plan` was shown to the owner.
- A release whose health check fails restores the previous one; report the engine's lines
  and the result. Rolling back across a schema change needs a restored backup first and
  `--allow-schema`, on the owner's instruction.

## 7. Proposals

When a case looks like it holds beyond this project, discuss it with the owner until the
rule is clear, then propose it from this session: draft the exact change in a framework
worktree from `main` (`git worktree add <dir> origin/main` in the framework checkout,
never the checkout itself), save `git diff` to a file, and run `pakem propose new --plan`
(details in `instruction-maintenance`, lessons intake). The framework is public: it refuses
private names, hosts, addresses, and home paths. Show the owner the text, run it again
with `--yes` on their go, and give them the pull request: merge accepts it, `pakem propose
revise <n>` answers a change request, `pakem propose reject <n> --reason ...` records a
rejection. When the owner reports the merge, tell them `pakem update` is due and run it
only on their instruction (section 3).
