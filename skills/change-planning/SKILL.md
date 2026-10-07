---
name: change-planning
description: Use when turning an idea, request, or problem into an approved plan - brainstorming, research, blueprints, phased plans, and OpenSpec proposals, designs, specs, and tasks - and when updating, verifying, syncing, or archiving OpenSpec changes. Complements, never replaces, the native openspec-* skills.
---

# Change Planning

Path from idea to approved, verifiable work. Every project uses native OpenSpec; the
owner approves before implementation.

## 1. Stages

1. **Intake** - restate the outcome, constraints, non-goals, and who decides.
2. **Brainstorm** - interview, research, challenge, recommend (section 2).
3. **Research and blueprint** - record findings, prototypes, and evidence in
   `research/` (section 3). Larger work gets a phased plan (section 4).
4. **Spec** - one OpenSpec change per independently shippable outcome (section 5).
5. **Approval** - the owner approves the change before any implementation.
6. **Implement** - topic branch; tasks completed with their evidence.
7. **Verify** - `verification.md` per change (section 7).
8. **Archive** - sync and archive (section 8).
9. **Lessons** - general lessons become framework candidates
   (`instruction-maintenance`).

Skip nothing silently. If the owner asks to move faster, say which stage is being
compressed and what risk that carries.

## 2. Brainstorm and discussion

The agent plays four roles at once:

- **Interviewer** - ask a few focused questions at a time until the problem,
  users, constraints, and acceptance are clear; then summarize back and get
  confirmation before proposing solutions.
- **Researcher** - read the existing code, specs, and `research/`, and check
  current official documentation or credible studies before forming an opinion.
- **Challenger** - name weak assumptions, contradictions, risks, and cheaper
  alternatives. Disagree plainly when the evidence supports it.
- **Advisor** - present two or three viable options with trade-offs and state
  which one you recommend and why.

Raise constraint fit (budget, deadline, who maintains and edits it after
handover, hosting and operations capability, scale, data sensitivity) when it
could change the choice; it is judgment, not a mandatory section.

A brainstorm is done when the owner has confirmed the problem statement and chosen
an option. Record it with the [discussion summary](references/discussion-summary.md)
format in `research/notes/`.

## 3. The research folder

```
research/
  notes/YYYY-MM-DD-<topic>.md     discussion summaries, audits, decisions, findings
  prototypes/<topic>/             throwaway builds and design alternatives
  evidence/<change-or-topic>/     screenshots, benchmark output, verification output
```

- `research/` is private: excluded from client deliverables (via
  `.git/info/exclude` or a private remote) unless the client buys the process.
- Reusable checks do not belong here; version them under `tests/` or
  `scripts/verify/`. Only their outputs go to `research/evidence/`.
- Never store production data or secrets in `research/`.

## 4. Phased plan for larger work

When work spans several changes (a new product, a backend from scratch, a
migration), write `research/notes/YYYY-MM-DD-<topic>-plan.md` using the
[phased plan](references/phased-plan.md) template before the first proposal: phases
in dependency order, each with scope, the OpenSpec changes it will produce, and a
verification gate. Get it approved, then propose changes phase by phase.

## 5. OpenSpec

### Native tooling authority

- Inspect the installed CLI (`openspec --version`), project configuration, and
  generated instructions before working. Use the native `core` profile unless the
  project documents a reason for another.
- Generated `openspec-*` skills and commands are tool-owned. Create and refresh them
  only with `openspec init` / `openspec update`; never hand-author or imitate them.
- Query the CLI (`openspec status`, `openspec instructions`, `openspec validate`)
  instead of reconstructing its rules from memory. Never claim OpenSpec ran when it
  did not. If the CLI is unavailable, say so and use a clearly labelled manual
  fallback.
- Keep `openspec/config.yaml` `context` and `rules` real and current (template:
  `assets/project/openspec-config.yaml.tmpl` in the `project-bootstrap` skill).

### Artifact quality

- **Proposal**: Why; Expected Outcome (observable result for the user or owner);
  What Changes; Non-goals; Capabilities affected; Impact. State whether the change
  adds a project capability (`Capabilities:` in `AGENTS.md`) and which standards
  that switches on.
- **Design**: context; goals and non-goals; numbered decisions, each with rejected
  alternatives and why; data, API, and failure behaviour; compatibility, migration,
  recovery, and rollback when relevant; risks; open questions.
- **Spec deltas**: testable requirements using SHALL with WHEN/THEN scenarios,
  including failure and permission scenarios. Never satisfy validation by wrapping a
  requirement's text in a generic scenario; write a real WHEN/THEN case or leave the
  warning recorded as follow-up work.
- **Tasks**: grouped in phases; each phase ends with an "Expected Result" line;
  every task names the evidence that proves it (command, test, HTTP check, browser
  check). The last task pushes the branch and reports the compare link.
- Keep an unknown cause explicitly unknown: plan reproduction and diagnosis first
  (`root-cause-debugging`), then update the design with the supported explanation.

## 6. Approval and change control

- Present the change for approval before implementation. Once approved, execute
  without asking again.
- Material changes to scope, behaviour, compatibility, or acceptance are surfaced
  and approved before they are implemented; update the active artifacts first.
- Mark a task complete only when its implementation and its named evidence are
  complete. A checked box is not proof.

## 7. Verification record

Each change keeps `verification.md` beside its artifacts
([template](references/verification-record.md)): revision tested, commands and
results with counts, environments and data used (synthetic), browser widths or
devices, what was not verified and why. Native `openspec validate` proves artifact
structure only, never application correctness.

## 8. Sync and archive

- Archive once the spec deltas are synced and the verification record is complete.
- Owner-side confirmation in production does not block archiving: record it under
  "Awaiting owner confirmation" in `PROJECT_STATE.md` with the date.
- If production later disproves something, open a new corrective change; never edit
  an archived change as a shortcut.
- Remove obsolete generated command or skill copies when `openspec update` replaces
  them.
