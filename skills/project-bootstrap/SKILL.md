---
name: project-bootstrap
description: Use when starting a new repository, adopting this framework in an existing project, or adding missing scaffolding - AGENTS.md, capabilities, PROJECT_STATE.md, research folder, OpenSpec config, formatter, linter, type checks, git hooks, CI, .env.example, or project templates.
---

# Project Bootstrap

Set up a repository so any agent can work in it to the owner's standard from the
first commit. For an existing project, follow
[adoption](references/adoption-checklist.md) instead of starting over.

## 1. Before scaffolding

Run `change-planning` intake and brainstorm first: outcome, users, constraints,
capabilities, and stack. Record the decision in `research/notes/`.

## 2. Steps for a new repository

1. **Git**: `git init -b main`; add `.gitignore` (start from
   [gitignore snippet](assets/project/gitignore.snippet)); follow
   [git conventions](references/git.md).
2. **Private artifacts**: add `PROJECT_STATE.md` and `JOURNAL.md` to
   `.git/info/exclude`. For client repositories also exclude `AGENTS.md`,
   `openspec/`, and `research/` (or keep them in a private remote) unless the client
   buys the process; for own and organization repositories commit `AGENTS.md` and
   `openspec/`.
3. **AGENTS.md** from the [template](assets/project/AGENTS.md.tmpl): purpose,
   `Capabilities:` (see section 3), commands, architecture map, project rules,
   design surface, overrides, references. Keep project content under 8,000
   characters. The kernel is installed globally, so `AGENTS.md` carries no copy of
   it; when a profile applies (organization or freelance), add its pinned block
   with `pakem project sync <repo> --profile <dir> --apply`. Write it when onboarding is
   settled, and keep it current: a change that alters a command, capability, the
   architecture map, a project rule, an override, or a runtime setting updates
   `AGENTS.md` in the same change.
4. **State**: `PROJECT_STATE.md` from the `state-and-handoff` skill templates.
5. **Research**: create `research/notes/`, `research/prototypes/`,
   `research/evidence/`.
6. **Layout**: follow [layout principles](references/layout-principles.md).
7. **Tooling**: formatter, linter, type checker, and test runner per
   [tooling baseline](references/tooling.md), runnable with one command each; start
   from [Python](assets/tooling/python/README.md) or
   [Node](assets/tooling/node/README.md) fragments and the
   [EditorConfig](assets/tooling/.editorconfig).
8. **Configuration**: typed settings and `.env.example`
   ([template](assets/project/env.example.tmpl)); see `backend-engineering` for the
   configuration standard.
9. **OpenSpec**: `openspec init` with the native `core` profile, then fill
   `openspec/config.yaml` from the [config template](assets/project/openspec-config.yaml.tmpl).
10. **Hooks**: install the framework `commit-msg` and `pre-push` hooks
    (`pakem hooks install <repo> --apply`) so commits stay Conventional and subject-only.
11. **CI**: add CI from the `release-delivery` skill templates for the declared
    capabilities; CI exists before the first feature lands.
12. **First commit**: `chore: bootstrap project` on `main`, then branch for the first
    change. The pre-push hook lets only documentation reach `main`, so the first push of
    a bootstrapped `main` that contains code is done once by the owner with
    `AF_ALLOW_PROTECTED_PUSH=1 git push -u origin main`; agents never set that variable.

## 3. Capabilities

Declare in `AGENTS.md`: `Capabilities: <comma-separated list>` using only these
names: `database`, `deployed`, `money`, `personal-data`, `background-jobs`,
`api-consumers`, `multi-user`, `llm`. The "always" standards apply without
declaration. Each capability switches on the standards listed in the framework's
capability registry; the relevant skills (`backend-engineering`,
`sensitive-operations`, `genai-engineering`, `release-delivery`, `ui-ux-quality`)
name them. Update the line whenever a change adds a capability.

Also declare `Delivery:`, who releases the project. `deployed` keeps meaning "runs as a
service" and switches on its runtime standards either way.

- `us`: we release it; `release-delivery` applies in full.
- `others (<who>)`: another person or team releases it. Our work ends at commits,
  branches, and the merge request: follow the team's branch, review, and release
  conventions; never change their pipelines, deploy files, or environments unless asked;
  propose release-related changes to them instead of making them.
- `nobody`: a library, tool, or experiment with no release beyond version tags.

When the line is missing or unclear, ask it as a gap; never assume `us`.

## 4. Other templates

- [Architecture decision record](assets/project/adr.md) under `docs/adr/`.
- [Data-source contract](assets/project/data-source-contract.md) for each external
  data source.
- [CHANGELOG](assets/project/CHANGELOG.md.tmpl) for projects that release versions.
