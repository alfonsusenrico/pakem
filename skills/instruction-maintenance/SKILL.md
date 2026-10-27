---
name: instruction-maintenance
description: Use when editing AGENTS.md, CLAUDE.md, GEMINI.md, rules, skills, or Pakem itself; when instructions conflict, exceed size budgets, or are ignored; or when a lesson from a project should become a framework rule.
---

# Instruction Maintenance

Instructions are context, not enforcement. Keep them short, specific, consistent,
and backed by mechanical checks where possible.

## 1. What belongs where

| Content | Home |
| --- | --- |
| Universal agent behaviour, true in personal, work, and client repositories alike | framework kernel (installed globally) |
| Rules for one context: personal, freelance, or an organization's work | that context's profile digest, pinned by each project |
| Procedures for a kind of work | a framework skill |
| Organization or client platform how-tos | that organization's or client's profile |
| Project facts an agent cannot discover from code | project `AGENTS.md` |
| Behaviour requirements | OpenSpec specs |
| Decisions with trade-offs | ADRs |
| Current status | `PROJECT_STATE.md` |
| History and evidence | git, OpenSpec archive, `JOURNAL.md`, `research/evidence/` |
| Agent-host memory (for example Claude Code memory) | Never the only home of a rule: it is per device, per agent, and per folder |

**One source of knowledge.** Every agent on every device reads the same framework,
installed by `pakem install` and pinned by each project; that is what keeps agents and
projects consistent. When the owner decides something that applies beyond one session,
write it into the framework (kernel, skill, or ADR) in the same piece of work, and into
the project's `AGENTS.md` when it is project-specific. An agent's own memory may keep a
reminder, never the rule itself. Small drift between agents is acceptable; a
contradiction is a defect to fix in the framework.

Never put repository overviews or directory dumps in instruction files; agents can
read the tree. Never restate the kernel or a skill in a project file.

## 2. Rule admission

A rule enters an always-loaded file only if it is: universal for that scope (for the
kernel: right in personal, work, and client repositories, or else a profile rule); not
discoverable from the code; not better enforced by a hook, lint, test, or
permission (if it is, enforce it and keep at most one line); tied to a real or
plausible failure; and does not contradict another rule. When adding a rule, look
for one to remove.

## 3. Budgets

- Kernel at most 9,500 characters; profile digests at most 2,000.
- Project `AGENTS.md` under 12,000 characters including pinned blocks (project
  content under 8,000).
- Skill description at most 350 characters, trigger first; skill body under 500
  lines; references one level deep.
- `PROJECT_STATE.md` under about 8 KB.

Check with `pakem lint` (framework) and `pakem project check <repo>` (projects) when the
framework tool is available.

## 4. Conflicts and overrides

- When two instructions conflict, stop and report both with their sources; do not
  pick silently.
- A profile, then a project, may change a labelled default: the profile in its digest,
  the project in `## Overrides`, each as one bullet naming the label and the reason. A
  profile may define labels of its own. Floors cannot be overridden.
- Retire a label instead of deleting it: `project check` warns on a retired label
  (`RETIRED_LABELS` in `tools/pakem_lib/project.py`) so existing projects keep passing.

## 5. Writing style for instructions

- Imperative, specific, checkable ("Run `uv run pytest` before pushing"), not
  aspirational ("Write good tests").
- Point to examples and files instead of describing them at length.
- Agent-drafted instruction text is reviewed by the owner before it is merged.

## 6. Lessons into the framework

When a case in any project looks like it holds beyond that project, discuss it with the
owner, then propose it with `pakem propose new` from that session, which still has the
context ([lessons intake](references/lessons-intake.md)). Never edit the framework from a
project session. Follow the [agent tooling policy](references/agent-tooling.md) before
adding or recommending skills, plugins, or tools.

## 7. Changing the framework itself

The framework is stable: change it only for a problem a real project hit, through an
accepted `pakem propose` proposal (its own pull request), with ADRs only for real
trade-offs. The `pakem` tool releases whenever an area is proven; kernel and skill text
waits for the next release, batched at most once a month. Each change is a fragment in
`changes/` (never a direct `CHANGELOG.md` edit); a release runs
`pakem changelog --release X.Y.Z --apply` (MAJOR only when a floor changes or an existing
install or project would break). Run its lint and tests before any commit. Install
only from merged `main`: fetch, check that the checkout is clean and equal to
`origin/main`, then run `pakem install --apply` with owner approval. Repositories need
`pakem project sync` only when a profile digest changes. `--allow-unmerged` and
`--allow-dirty` are for deliberately testing a branch, never for a routine install.
