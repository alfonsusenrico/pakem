# Lessons Intake

Status: Active
Applies to: turning project experience into framework rules
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile section 14

1. **Capture** - after an incident, a repeated correction, or a pattern that worked
   well, record it in the project: incident record, ADR, spec, or `AGENTS.md` rule.
2. **Generalize** - ask whether it holds beyond this project, beyond this stack, and
   beyond this client. If it only holds for an organization, it belongs in that
   organization's profile.
3. **Propose** - note it as a framework candidate in the framework repository's
   backlog (`docs/backlog.md`): the lesson, its evidence (project, date, link), the
   proposed rule and home (kernel, skill, reference, template, tooling), and the rule
   it replaces or makes obsolete.
4. **Decide** - the owner accepts, adapts, or rejects. Decisions with trade-offs get
   an ADR.
5. **Release** - change the framework with a `changes/` fragment, release it with
   `af changelog --release X.Y.Z --apply`, run lint and tests, install, and sync
   adopted projects.
6. **Prune** - review rules at least quarterly; remove rules that no longer prevent a
   real failure or that tooling now enforces.

Prefer enforcement over prose: if a lesson can become a hook, lint rule, test, CI
check, or template default, do that instead of adding a sentence.
