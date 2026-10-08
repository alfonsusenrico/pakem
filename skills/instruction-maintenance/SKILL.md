---
name: instruction-maintenance
description: Use when editing AGENTS.md, CLAUDE.md, GEMINI.md, rules, skills, or the agent framework itself; when instructions conflict, exceed size budgets, or are ignored; or when a lesson from a project should become a framework rule.
---

# Instruction Maintenance

Instructions are context, not enforcement. Keep them short, specific, consistent,
and backed by mechanical checks where possible.

## 1. What belongs where

| Content | Home |
| --- | --- |
| Universal agent behaviour | framework kernel (installed globally) |
| Procedures for a kind of work | a framework skill |
| Organization or client platform how-tos | a profile repository |
| Project facts an agent cannot discover from code | project `AGENTS.md` |
| Behaviour requirements | OpenSpec specs |
| Decisions with trade-offs | ADRs |
| Current status | `PROJECT_STATE.md` |
| History and evidence | git, OpenSpec archive, `JOURNAL.md`, `research/evidence/` |

Never put repository overviews or directory dumps in instruction files; agents can
read the tree. Never restate the kernel or a skill in a project file.

## 2. Rule admission

A rule enters an always-loaded file only if it is: universal for that scope; not
discoverable from the code; not better enforced by a hook, lint, test, or
permission (if it is, enforce it and keep at most one line); tied to a real or
plausible failure; and does not contradict another rule. When adding a rule, look
for one to remove.

## 3. Budgets

- Kernel at most 9,500 characters; digest at most 2,400.
- Project `AGENTS.md` under 12,000 characters including pinned blocks (project
  content under 8,000).
- Skill description at most 350 characters, trigger first; skill body under 500
  lines; references one level deep.
- `PROJECT_STATE.md` under about 8 KB.

Check with `af lint` (framework) and `af project check <repo>` (projects) when the
framework tool is available.

## 4. Conflicts and overrides

- When two instructions conflict, stop and report both with their sources; do not
  pick silently.
- A project deviates from a labelled default only through `## Overrides`, one bullet
  naming the label and the reason. Floors cannot be overridden.

## 5. Writing style for instructions

- Imperative, specific, checkable ("Run `uv run pytest` before pushing"), not
  aspirational ("Write good tests").
- Point to examples and files instead of describing them at length.
- Agent-drafted instruction text is reviewed by the owner before it is merged.

## 6. Lessons into the framework

Follow [lessons intake](references/lessons-intake.md) to move a project lesson into
the framework, and [agent tooling policy](references/agent-tooling.md) before adding
or recommending skills, plugins, or tools.

## 7. Changing the framework itself

The framework repository records decisions as ADRs, every release in `CHANGELOG.md`,
and versions per semantic versioning (MAJOR for floor, marker, or skill-name
changes). Run its lint and tests before any commit. Install only from merged `main`:
fetch, check that the checkout is clean and equal to `origin/main`, then run
`af install --apply` with owner approval, and `af project sync` in adopted
repositories. `--allow-unmerged` and `--allow-dirty` are for deliberately testing a
branch, never for a routine install.
