# Lessons Intake

Status: Active
Applies to: turning project experience into framework rules
Last reviewed: 2026-10-22
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
   checkout itself, which installs and hooks run from). It refuses a change that does not
   apply and names earlier proposals on the same subject. Give the owner the link.
4. **Decide** - the owner accepts, asks for changes (`pakem propose revise <n>`), or
   rejects (`pakem propose reject <n> --reason ...`). Only after an acceptance in the
   session: `pakem propose accept <n>` opens the pull request. Decisions with trade-offs
   get an ADR.
5. **Release** - the owner merges the pull request; then run `pakem update` so every
   agent loads the change on its next turn. Version releases batch the CHANGELOG
   (`pakem changelog --release X.Y.Z --apply`), at most one a month.
6. **Prune** - review rules at least quarterly; remove rules that no longer prevent a
   real failure or that tooling now enforces.

Prefer enforcement over prose: if a lesson can become a hook, lint rule, test, CI
check, or template default, do that instead of adding a sentence.
