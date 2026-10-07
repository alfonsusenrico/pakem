# Git Conventions

Status: Active
Applies to: every repository
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 4; ADR 0004 of the framework; Conventional Commits 1.0.0

- Default branch `main`. Topic branches `<type>/<kebab-topic>` (`feat/checkout-quota`).
- Commits: `type(scope): summary` - types feat, fix, docs, refactor, test, chore, ci,
  perf, build, revert; imperative, lower-case summary, at most 72 characters; no body,
  no trailers, no AI attribution. Rationale goes in the OpenSpec change or merge
  request, not the commit.
- Direct pushes to a protected branch only for documentation-only ranges (`*.md`,
  `docs/**`); everything else goes through a branch and a merge request the owner
  merges.
- Protected branches default to `main` and `master`. A repository that integrates
  through another branch (for example `development`) declares the full set with
  `af hooks install <repo> --protect development --protect main --apply`. The list
  replaces the default, so name `main` too when it stays protected. The values live
  in the repository's `agentframework.protectedBranch` git config and are read by the
  pre-push hook and the Claude Code and Antigravity guard; Codex rules are static and
  always protect `main`, `master`, and `development`.
- Never rewrite shared history; force-push only your own unmerged topic branch, and
  prefer `--force-with-lease`.
- Releases are tagged `vMAJOR.MINOR.PATCH` on `main` when the project ships versions.
- Hooks (`commit-msg`, `pre-push`) live in `.githooks/` and are enabled with
  `git config core.hooksPath .githooks`; never bypass them. The owner override for a
  code push to a protected branch is `AF_ALLOW_PROTECTED_PUSH=1` (the older
  `AF_ALLOW_MAIN_PUSH=1` still works); commit messages are checked either way.
- Client repositories must not receive committed hook files: install with
  `af hooks install <repo> --local-only --apply`, which adds `.githooks/` to
  `.git/info/exclude`.
- `core.hooksPath` is shared by every worktree of the repository and the relative
  `.githooks` resolves against each worktree's own root. A worktree without its own
  `.githooks/` runs no hooks, silently: until the branch that commits `.githooks/`
  is merged into the worktree's branch, or, for a local-only install, until
  `af hooks install <worktree> --local-only --apply` is run in that worktree. The
  exclude file and protected-branch config are shared by all worktrees.
- One author identity per context (personal, organization, client) configured per
  repository.
