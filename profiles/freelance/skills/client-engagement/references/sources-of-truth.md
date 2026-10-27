# Sources of Truth

Status: Active
Applies to: work inside a client's or partner team's repository
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: a freelance marketplace backend engagement (project AGENTS.md project rules and research notes, 2026-09 to 2026-10); owner Working Standard Profile sections 1 and 3

## Default order

Use this order unless the engagement records another in its `AGENTS.md`:

1. **Recorded decisions**: the client PM's or owner's decisions, recorded with who
   and when in an OpenSpec change or the decision log.
2. **Maintained design artifacts**: the data model (ERD), API contract, or designs
   the client keeps current. Read the latest copy before data or API work; an
   announced update supersedes earlier readings.
3. **The live product**: the latest deployed frontend or app, reviewed critically as
   the starting point for flows and visible features.
4. **Supplied templates or reference repositories**: reusable architecture only.
   Their entities, fields, routes, and defaults are not requirements.

Shared development databases and teammates' code are evidence of current state, not
a contract.

## Reading the live product

- Judge real wiring from shipped code (API paths, payload keys, generated client
  definitions), not from rendered numbers, which are often mock content.
- Note which screens still use mock data; an endpoint they will call can be broken
  without anyone noticing yet.
- Record what was inspected, from which build or URL, and when.

## Conflicts and gaps

- When two sources disagree, or the product needs something no source defines (a
  field, relation, rule, or state), stop and write a question. Never pick silently.
- A field or relation the product needs but the data model lacks gets a proposed
  design and PM confirmation before implementation.
- While a question is open, implement nothing that depends on the answer; record the
  assumption other work relies on and where it would change.

## Question format

Each question for the PM, delivered through the owner:

- **Question**: one decision, phrased so it can be answered in a sentence.
- **Evidence**: what each source says, with location (file, screen, build).
- **Options**: two or three, with the consequence of each.
- **Recommendation**: the option you would choose and why.
- **Blocks**: what work waits on the answer.

Group questions by decision area and put urgent ones first.
