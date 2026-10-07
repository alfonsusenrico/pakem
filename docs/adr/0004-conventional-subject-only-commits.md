# 0004. Conventional Commits, subject line only

- Status: Accepted
- Date: 2026-10-07

## Context

History mixed plain sentences, Conventional Commits, invented types (`refine:`,
`ui(...)`), commit bodies, and AI attribution trailers, although the previous
baseline asked for subject-only commits without attribution.

## Decision

Subjects follow `type(scope): summary` with types feat, fix, docs, refactor, test,
chore, ci, perf, build, revert. No body and no trailers. A `commit-msg` hook and a
`pre-push` hook enforce the format; git-generated merge and revert subjects pass.
Direct pushes to `main` are allowed only for documentation-only ranges.

## Consequences

History becomes machine-readable for changelogs and version bumps. Rationale that
used to go in commit bodies belongs in plans, ADRs, or merge request descriptions.
Projects may override this default for client conventions in `## Overrides`.
