# Git Conventions

Status: Active
Applies to: every repository
Last reviewed: 2026-10-28
Review by: 2027-04-07
Sources: owner Working Standard Profile section 4; ADR 0004 of the framework; Conventional Commits 1.0.0

- Default branch `main`. Topic branches `<type>/<kebab-topic>` (`feat/checkout-quota`).
- Commits: `type(scope): summary` - types feat, fix, docs, refactor, test, chore, ci,
  perf, build, revert; imperative, lower-case summary, at most 72 characters; no body,
  no trailers, no AI attribution. Rationale goes in the OpenSpec change or merge
  request, not the commit.
- `main` stays deployable: pipelines deploy staging from it by default. Code changes
  go through a topic branch and one merge request per finished piece of work: the agent
  commits locally and, once the work is done and the owner accepted it, pushes the
  branch, opens the merge request (`gh pr create`, `glab mr create`) with the specs and
  code together, and reports its URL; the owner reviews and merges. Plans and specs never
  get a merge request of their own. Agents never merge or approve.
- Documentation-only ranges (`*.md`, `docs/**`) may be pushed directly to a protected
  branch. The repository's pre-push hook enforces this; host rules only block force
  pushes and deletions of protected branches.
- Protected branches default to `main` and `master`. A repository that integrates
  through another branch (for example `development`) declares the full set with
  `pakem hooks install <repo> --protect development --protect main --apply`. The list
  replaces the default, so name `main` too when it stays protected. A project that
  allows direct code pushes records `default:branching` in `## Overrides` and installs
  with `--protect-none`. The values live in the repository's
  `pakem.protectedBranch` git config and are read by the pre-push hook and
  the Claude Code and Antigravity guard; Codex rules are static and always forbid
  force-pushing or deleting `main`, `master`, and `development`.
- Never rewrite shared history. Ask the owner before any force push, including on
  your own topic branch.
- Once a merge request merges, delete its branch locally and on the remote
  (`default:prune-merged`). A branch counts as merged only when its tip is part of the
  default branch on origin: check with `git merge-base --is-ancestor <branch>
  origin/main`, then `git branch -D <branch>` and `git push origin --delete <branch>`.
  A squash-merged branch never passes that check, so ask the owner before deleting it.
  Never delete the default branch, a protected branch, or a branch checked out in a
  worktree. A project keeps single branches with a line in its `AGENTS.md`, for example
  `Keep branches: release/*, demo`, and opts out entirely with a `default:prune-merged`
  bullet in `## Overrides`. The owner can also keep one in the session. On the forge,
  turn on deleting head branches after a merge (GitHub: `delete_branch_on_merge`) so the
  remote side happens by itself. `pakem update` prunes the framework checkout the same
  way.
- Releases are tagged `vMAJOR.MINOR.PATCH` on `main` when the project ships versions.
- Hooks (`commit-msg`, `pre-push`) live in `.githooks/` and are enabled with
  `git config core.hooksPath .githooks`; never bypass them. The owner override for a
  code push to a protected branch is `AF_ALLOW_PROTECTED_PUSH=1` (the older
  `AF_ALLOW_MAIN_PUSH=1` still works); commit messages are checked either way.
- Client repositories must not receive committed hook files: install with
  `pakem hooks install <repo> --local-only --apply`, which adds `.githooks/` to
  `.git/info/exclude`.
- `core.hooksPath` is shared by every worktree of the repository and the relative
  `.githooks` resolves against each worktree's own root. A worktree without its own
  `.githooks/` runs no hooks, silently: until the branch that commits `.githooks/`
  is merged into the worktree's branch, or, for a local-only install, until
  `pakem hooks install <worktree> --local-only --apply` is run in that worktree. The
  exclude file and protected-branch config are shared by all worktrees.
- One author identity per context (personal, organization, client) configured per
  repository.
