---
name: af-update
description: Use when the owner says the agent framework was updated or installed, runs /af-update, or asks the agent to reload its framework rules in a running session. Re-reads the installed kernel and skills, reports what changed, and applies it from the next action.
---

# Reload the framework in a running session

A session reads its kernel once, when it starts; an install afterwards changes the files,
not what this session already loaded. This skill brings a running session up to date
without starting a new one. It only reads and reports; it never installs, pushes, or
changes project files.

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
