# Adopting the Framework in an Existing Repository

Status: Active
Applies to: repositories created before the framework or with a legacy baseline block
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: framework review of owner repositories (2026-10-07)

Do this as its own change on a `chore/adopt-agent-framework` branch.

1. Replace any legacy `<!-- owner-baseline:start -->` block with the pinned digest
   (`af project sync <repo> --replace-legacy --apply`).
2. Remove text in `AGENTS.md` that restates the kernel or skills, absolute home paths,
   and dangling references. Move spec-like content into `openspec/specs/` or `docs/`,
   decision logs into `research/notes/`, and benchmark numbers into
   `research/evidence/`. Target: project content under 8,000 characters.
3. Add `Capabilities:` and `## Overrides` (only real deviations, each with a reason).
4. Compact `PROJECT_STATE.md` to the template (under about 8 KB); move dated history to
   `JOURNAL.md`.
5. Create `research/`; move out-of-repo harnesses and prototypes into the repository
   (`tests/e2e/`, `scripts/verify/`, `research/prototypes/`).
6. Fill `openspec/config.yaml` context and rules; run `openspec update`; triage open
   changes (archive verified ones, record owner confirmations in `PROJECT_STATE.md`).
7. Add the formatter and linter configuration and run them in one formatting-only
   commit.
8. Install hooks (`af hooks install <repo> --apply`); add or update CI from the
   templates for the declared capabilities.
   - Integration branch other than `main` (for example `development`): add
     `--protect development --protect main`; the list replaces the default
     `main`/`master` set, so name every branch that stays protected.
   - Client repository: add `--local-only` so `.githooks/` is listed in
     `.git/info/exclude` and never committed to the client's repository.
   - Worktrees: `core.hooksPath` applies to every worktree, but each worktree needs
     its own `.githooks/`. Run the install in every existing worktree (local-only),
     or expect no hooks in other worktrees until the branch adding `.githooks/` is
     merged into theirs.
9. Verify: `af project check <repo>`, the project's tests, lint, and build.
