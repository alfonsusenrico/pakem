---
name: af-update
description: Use when the owner says the agent framework was updated or installed, runs /af-update, or asks the agent to reload its framework rules in a running session. Re-reads the installed kernel and skills, reports what changed, and aligns the current project with it.
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

If the manifest is missing, say the framework is not installed with `af install` on this
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

## 4. Align the current project (one pass)

Only the project of this session; another session aligns its own.

1. **Timing.** If a task is in progress, finish it first and list the alignment items in
   the report. A new rule that changes the task's next step (for example "do not open the
   merge request yet") applies immediately.
2. **Find drift.** Run `af project check` for the project, and compare its `AGENTS.md`,
   `docs/`, and OpenSpec config with the re-read rules.
3. **Fix the mechanical parts** without asking: the pinned framework block
   (`af project sync`), and wording that contradicts the new rules. Commit locally on the
   current work branch, or on a `docs/<topic>` branch when no work is in progress; push
   and open a merge request only when the owner accepts, as for any work.
4. **Ask only real gaps.** Where a new rule needs a project-specific answer (for example
   which billed services the project uses and their budget), use the gap protocol from
   `change-planning`: one message with every question and its default; follow-ups only
   for answers that fail or conflict; at most two rounds; anything still open is recorded
   as pending.
5. **Finish** when `af project check` passes and every gap is answered, provisional, or
   pending. Report the changes made, the decisions recorded, and anything left pending,
   then return to the task. Do not keep improving beyond the update.
