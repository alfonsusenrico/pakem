# Lessons Intake

Status: Active
Applies to: turning project experience into framework rules
Last reviewed: 2026-10-23
Review by: 2027-04-07
Sources: owner Working Standard Profile section 14

1. **Capture** - after an incident, a repeated correction, or a pattern that worked
   well, record it in the project: incident record, ADR, spec, or `AGENTS.md` rule.
2. **Generalize** - ask whether it holds beyond this project, beyond this stack, and
   beyond this client. If it only holds for an organization, it belongs in that
   organization's profile.
3. **Propose** - in the session that has the case, discuss it with the owner until the
   rule is clear, then run `pakem propose new`: the rule as it should read, the case,
   why it holds beyond this project, its home (kernel, skill, reference, template,
   tooling, ADR), evidence, the rule it replaces, whether a check could enforce it, and
   the exact change as a unified diff drafted in a framework worktree from `main`
   (`git worktree add <dir> origin/main` in the framework checkout; never edit the
   checkout itself, which installs and hooks run from). It opens a pull request after lint
   and tests pass, refuses a change that does not apply, and names earlier proposals on the
   same subject. Give the owner the link.
4. **Decide** - the owner merges it (accept), asks for changes (`pakem propose revise
   <n>`), or rejects it (`pakem propose reject <n> --reason ...`). Decisions with
   trade-offs get an ADR.
5. **Release** - after the merge, run `pakem update` so every agent loads the change on its
   next turn. Version releases batch the CHANGELOG
   (`pakem changelog --release X.Y.Z --apply`), at most one a month.
6. **Prune** - review rules at least quarterly; remove rules that no longer prevent a
   real failure or that tooling now enforces.

Prefer enforcement over prose: if a lesson can become a hook, lint rule, test, CI
check, or template default, do that instead of adding a sentence.
