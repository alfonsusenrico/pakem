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
- Direct pushes to `main` only for documentation-only ranges; everything else goes
  through a branch and a merge request the owner merges.
- Never rewrite shared history; force-push only your own unmerged topic branch, and
  prefer `--force-with-lease`.
- Releases are tagged `vMAJOR.MINOR.PATCH` on `main` when the project ships versions.
- Hooks (`commit-msg`, `pre-push`) live in `.githooks/` and are enabled with
  `git config core.hooksPath .githooks`; never bypass them.
- One author identity per context (personal, organization, client) configured per
  repository.
