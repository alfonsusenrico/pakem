---
name: change-review
description: Use before committing, pushing, or declaring work ready, and when asked to review a diff, branch, or merge request. Checks correctness, security, data safety, capability standards, tests and evidence, and maintainability, and reports findings with severity and file references.
---

# Change Review

## 1. Before declaring your own work ready

1. Run the project's format check, lint, type check, tests, and build exactly as CI
   does; record results against the revision tested.
2. Walk the [review checklist](references/checklist.md) over the full diff
   (`git diff <base>...HEAD` plus untracked files).
3. Confirm every task's named evidence exists and the change's `verification.md` is
   complete ([testing standard](references/testing.md)).
4. Check capabilities: if the diff adds one (database, deployed, money, personal-data,
   background-jobs, api-consumers, multi-user, llm), its standards are present or
   recorded as debt.
5. Report passed, failed, not run, and blocked separately. Never call work ready with
   failing or skipped required checks.

## 2. Reviewing someone else's change

- Read the linked plan or OpenSpec change first; review against its acceptance.
- Trace each changed path through callers and contracts; do not review lines in
  isolation.
- Report findings as described in section 3; confirm each finding against the code
  before reporting it. Separate verified defects from questions and suggestions.
- Do not rewrite the change yourself unless asked.

## 3. Findings format

For each finding: severity (blocker, major, minor, nit), `file:line`, what is wrong,
the concrete failure scenario (inputs or state leading to the wrong result), and the
suggested fix. Order by severity. If nothing survives verification, say so.

## 4. Related skills

- Reliability, data, and API checks: `backend-engineering`.
- Secrets, migrations, production, deletion: `sensitive-operations`.
- UI and accessibility evidence: `ui-ux-quality`.
- LLM features: `genai-engineering`.
