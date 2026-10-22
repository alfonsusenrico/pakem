# 0011. `pakem`: a standard core with adaptive edges

- Status: Accepted (owner, 2026-10-21); amended 2026-10-22 (the command is `pakem`, after
  the 2.0.0 rename; decision 8: commands prepare what they need, approved per session)
- Date: 2026-10-21

## Context

The framework exists to make the owner's development and automation easy. In practice
(2026-10-14 to 2026-10-21) agents still did the standard work by hand:
- improvised scripts for secrets and server setup;
- copied gateway limits without reading the gateway's documentation;
- a release pipeline tied to a billed CI;
- production commands that the host's safety checks blocked;
- many owner steps typed from chat.

Each run re-derived the same procedure, and each mistake came from that.

The owner wants one tool, `pakem`, to do every standard operation: set up any device and
any server, initialise or adopt a project (GitHub or GitLab), wire its CI, release and
roll it back. Agents should use `pakem` for those operations instead of individual commands.
Projects also differ in ways no tool should hard-code:
- one bot that only one process may poll;
- a backup hook's contents;
- a database alias on a shared network;
- what "healthy" means;
- a data move.

Those gaps are where the coding agent's judgment and the owner's knowledge are needed.
The owner called this documented automation with dynamic adjustment, and asked that gap
gathering be target-oriented, with a firm finish, so agents do not interview in loops.

## Decision

1. **Standard core.** `pakem` implements every operation that is the same in every project:
   - device and host setup, and keys;
   - project initialisation and adoption;
   - CI wiring for each forge;
   - secrets handling;
   - release, deploy, rollback and history;
   - setup checks.

   It never contains project- or case-specific logic, names, or infrastructure.
2. **Adaptive edges.** Every `pakem` command declares the gaps it leaves open:
   project- or case-specific decisions, each with a question, its reason, options, a
   default, whether it is required, the file and key where the answer is stored, and the
   check that proves it is filled. The coding agent fills gaps with the developer
   through the gap protocol (`docs/pakem-command-map.md`) and writes answers only into those
   places. `pakem project check` validates the result against the standard.
3. **Firm finish.**
   - Gap gathering is done when every required gap is in one of three states, each
     recorded with its reason: **answered**; **provisional** (a stated, reversible
     working assumption with a revisit trigger); or **pending** (waiting for outside
     information, with what is missing, who or what provides it, and a date to ask
     again).
   - The agent asks only listed gaps, in one batch, offering defaults. It asks a follow-up
     only when an answer fails validation or contradicts another, and stops after two
     rounds; anything still unclear becomes pending.
   - Pending gaps are not asked again until they are due.
4. **Agents use `pakem` for covered operations.** Once an area is stable, the kernel rule
   applies to it: use `pakem`, never hand-run the equivalent. If `pakem` lacks something or
   fails, report it and fix `pakem`; never work around it.
5. **Conventions** for every command:
   - interactive for people;
   - `--plan` (what would happen), `--json` (machine output) and explicit flags with no
     prompts, for agents;
   - safe to re-run;
   - stable exit codes;
   - owner-only steps reported as "owner action required" and verified afterwards;
   - production actions need an explicit flag and the owner's instruction;
   - secrets are never printed;
   - only free services unless the project approves a billed one (`default:no-billed`).
6. **Decisions that wait on the outside world.** Each gap declares the stages it blocks
   (for example deploy setup and release, but not code or CI), so `pakem status` shows how
   far a project can go now.
   - Agents work on every stage that is not blocked, and keep a pending choice open in
     the design (settings instead of constants, clean module boundaries).
   - A due decision (its date passed, or the work reached a stage it blocks) is raised
     at session start as one question.
   - `pakem decide <gap> <answer>` records it; `pakem` regenerates the affected files and lists
     provisional work to revisit.
   - Decisions live in the committed `.pakem/decisions.toml`, so every agent on every device
     sees them, with a generated "Open decisions" list in `AGENTS.md`. Client
     repositories keep both out of deliverables.
7. **Agents know when the framework changed.**
   - `pakem status` compares the installed framework with merged `main` (network at most
     every few hours, cached) and the project's pinned version with the installed one.
   - A session-start hook installed by `pakem enforce` puts a short notice into each
     session, with what changed and what to run. Hosts without hooks follow a kernel
     line instead.
   - A local check on each new message, without network, detects an install made
     mid-session and names the kernel and skill files to re-read.
   - Agents only notify by default. With the owner's standing approval
     (`pakem config set update apply`) they install updates themselves, only from merged
     `main`.
8. **Commands prepare what they need; the owner approves per session.** Each command
   declares its needs (tools such as `gh` or `glab`, a container runtime, the device key,
   forge logins) and provides what is missing as part of its own plan: no separate manual
   setup first. `pakem setup device` stays only to prepare a fresh machine in one go.
   - The owner's instruction in the session to run a command approves the installs that
     command lists, which pakem prints before installing; there is no standing approval.
     This is how the existing floor's "owner's approval" for global tooling is given, so
     the floor text does not change.
   - What only the owner can do stays an owner action: browser logins, sudo without a
     terminal, the device key's passphrase, and what the package manager cannot provide.
   - The device key has a passphrase by default and is used through ssh-agent on macOS,
     Linux, and WSL (the macOS Keychain unlocks it without prompts); a device nobody can
     unlock is created with `--no-passphrase`, recorded per device. Native Windows is not
     supported; WSL is.
9. **Release pace.** The `pakem` tool may release as often as its areas mature; the kernel and
   skills keep ADR 0007's monthly batch.
10. **Maturity per area:** `draft`, then `experimental` (usable, may change), then
   `stable` (complete, tested end to end, documented in the `pakem` skill; the kernel rule
   applies). `pakem help` shows each area's stage.
11. **Repeated answers become standard.** When several projects fill the same gap the
   same way, the answer is proposed as an `pakem` default through lessons intake.

## Consequences

- `pakem` grows from a framework installer into the workflow tool. Its existing commands
  (`install`, `doctor`, `lint`, `project`, `hooks`, `enforce`, `eval`, `schedule`,
  `changelog`) keep working and are regrouped in the command map.
- A new skill, `pakem`, documents every command, its gaps and errors; the
  `release-delivery`, `project-bootstrap` and `sensitive-operations` skills point to it
  instead of describing manual steps.
- `AGENTS.md` is written when onboarding is settled and kept current in the same change
  that alters it (kernel section 7); `pakem project check` flags what it can verify.
- Proof: once setup, init and release are stable, llm-gateway and personal-agent are
  revised to the standard using only `pakem`; then portfolio and cash-flow-tracker. A step
  that needs a manual command is a gap in `pakem`, not in the project.
- Release kinds: deployed services, distributed artifacts (executables), and projects
  with no release share checks, versions and tags; only delivery differs.
- ADR 0010's deployment plan is rebuilt around these commands.
