# 0011. `af`: a standard core with adaptive edges

- Status: Proposed
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

The owner wants one tool, `af`, to do every standard operation: set up any device and
any server, initialise or adopt a project (GitHub or GitLab), wire its CI, release and
roll it back. Agents should use `af` for those operations instead of individual commands.
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

1. **Standard core.** `af` implements every operation that is the same in every project:
   - device and host setup, and keys;
   - project initialisation and adoption;
   - CI wiring for each forge;
   - secrets handling;
   - release, deploy, rollback and history;
   - setup checks.

   It never contains project- or case-specific logic, names, or infrastructure.
2. **Adaptive edges.** Every `af` command declares the gaps it leaves open:
   project- or case-specific decisions, each with a question, its reason, options, a
   default, whether it is required, the file and key where the answer is stored, and the
   check that proves it is filled. The coding agent fills gaps with the developer
   through the gap protocol (`docs/af-command-map.md`) and writes answers only into those
   places. `af check` validates the result against the standard.
3. **Firm finish.**
   - Gap gathering is done when `af check --gaps` reports no open required gaps and each
     answer is recorded with its reason.
   - The agent asks only listed gaps, in one batch, offering defaults. It asks a follow-up
     only when an answer fails validation or contradicts another, and stops after two
     rounds.
   - Anything still unclear is recorded as an open item, and the work continues.
4. **Agents use `af` for covered operations.** Once an area is stable, the kernel rule
   applies to it: use `af`, never hand-run the equivalent. If `af` lacks something or
   fails, report it and fix `af`; never work around it.
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
6. **Maturity per area:** `draft`, then `experimental` (usable, may change), then
   `stable` (complete, tested end to end, documented in the `af` skill; the kernel rule
   applies). `af help` shows each area's stage.
7. **Repeated answers become standard.** When several projects fill the same gap the
   same way, the answer is proposed as an `af` default through lessons intake.

## Consequences

- `af` grows from a framework installer into the workflow tool. Its existing commands
  (`install`, `doctor`, `lint`, `project`, `hooks`, `enforce`, `eval`, `schedule`,
  `changelog`) keep working and are regrouped in the command map.
- A new skill, `af`, documents every command, its gaps and errors; the
  `release-delivery`, `project-bootstrap` and `sensitive-operations` skills point to it
  instead of describing manual steps.
- Proof: once setup, init and release are stable, llm-gateway and personal-agent are
  revised to the standard using only `af`; then portfolio and cash-flow-tracker. A step
  that needs a manual command is a gap in `af`, not in the project.
- Release kinds: deployed services, distributed artifacts (executables), and projects
  with no release share checks, versions and tags; only delivery differs.
- ADR 0010's deployment plan is rebuilt around these commands.
