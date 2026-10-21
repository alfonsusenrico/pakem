---
name: pakem-update
description: Use when the owner says the agent framework was updated or installed, runs /pakem-update, or asks the agent to reload its framework rules in a running session. Re-reads the installed kernel and skills, reports what changed, and aligns the current project with it.
---

# Reload the framework in a running session

A session reads its kernel once, when it starts; an install afterwards changes the files,
not what this session already loaded. This skill brings a running session up to date
without starting a new one, then aligns the project it is working on. It never installs,
pushes, opens a merge request, deletes anything, or acts on production; those still need
the owner.

## 1. Find this host's installed files

Read `~/.local/state/agent-framework/manifest.json`:

- `framework_version` and `installed_at`: the installed release and when it was written;
- `entries`: every installed file, with `host`, `kind` (`instructions` or `skill`),
  `path`, and `source`.

Use the entries whose `host` is this agent:

| Agent | `host` | Instructions file | Skills folder |
| --- | --- | --- | --- |
| Claude Code | `claude` | `~/.claude/CLAUDE.md` | `~/.claude/skills` |
| Codex | `codex` | `~/.codex/AGENTS.md` | `~/.agents/skills` |
| Gemini / Antigravity | `antigravity` | `~/.gemini/GEMINI.md` | `~/.gemini/config/skills` |

If the manifest is missing, say the framework is not installed with `pakem install` on this
device and stop.

## 2. Re-read

1. The instructions file, in full. It replaces the kernel this session loaded.
2. Every skill this session has already used, and every skill the current task needs
   next (`SKILL.md`, and a reference file only when the task needs it).
3. The project's `AGENTS.md`, if the project's pinned framework block changed.

## 3. Report and apply

Reply with one short message:

- the installed version and time;
- the rules that differ from what this session followed before, one line each;
- in-progress work those rules affect, and what changes (for example: "the branch stays
  local; the merge request waits until you accept the work");
- "No change" when nothing differs from what this session loaded.

Then follow the re-read rules from the next action. Where an earlier instruction from
the owner in this session conflicts with a new rule, the owner's instruction still wins
(kernel section 1); name the conflict instead of choosing silently.

## 4. Align the current project (one pass, with judgment)

Only the project of this session; another session aligns its own. Use what this session
already knows about the project: the update neither erases that knowledge nor overrides
it silently.

### Know whose project it is

Decide from the project's `AGENTS.md` (a work or organization profile block, the personal
profile's client rules, or neither), its `## Overrides`, its git remote, and this
session's context:

- **own**: the owner's personal project;
- **work**: an organization project shared with colleagues under its profile;
- **client**: a client's repository, where the client's conventions win.

### Weigh each difference

| The update changes | own | work | client |
| --- | --- | --- | --- |
| How the agent works (when to push, how to interview) | Adopt now | Adopt unless a project override says otherwise | Adopt where it does not conflict with the client's conventions |
| Project text (pinned framework block, wording that contradicts a rule) | Fix and commit locally | Propose the edit; change it after the owner confirms | Do not edit the client's files; propose an `## Overrides` entry or note to the owner |
| Structure or code (deployment design, dependencies, CI, file layout) | Never during an update: record a proposed follow-up with its impact | Same, naming who else is affected | Same; the client decides |

Rules for every case:

- **Nothing is skipped silently.** Each difference ends as applied, proposed, or kept
  with its reason ("kept: override `default:mr-policy`").
- **No refactors.** Alignment edits only text and configuration; anything touching code,
  CI, deployment, or more than a few files becomes a proposal through `change-planning`.
- **Overrides hold.** A labelled default the project overrides does not change; say so.
- **Knowledge counts.** When this session knows a project fact that conflicts with a new
  rule, name the conflict and ask; never pick a side silently.

### Steps

1. **Timing.** If a task is in progress, finish it first and list the alignment items in
   the report. A new rule that changes the task's next step (for example "do not open the
   merge request yet") applies immediately.
2. **Find differences.** Run `pakem project check` for the project, and compare its
   `AGENTS.md`, `docs/`, and OpenSpec config with the re-read rules.
3. **Act by the table above.** Commit allowed fixes locally on the current work branch,
   or on a `docs/<topic>` branch when no work is in progress; push and open a merge
   request only when the owner accepts, as for any work.
4. **Ask only real gaps,** with a gap interview (`change-planning`): one message with
   every question and its default, follow-ups only for answers that fail or conflict, at
   most two rounds; anything still open is recorded as pending.
5. **Finish** when `pakem project check` passes and every difference is applied, proposed,
   or kept with a reason. Report them in three short lists (applied, proposed, kept), then
   return to the task. Do not keep improving beyond the update.
