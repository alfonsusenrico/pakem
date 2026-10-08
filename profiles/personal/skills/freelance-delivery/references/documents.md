# Engagement Documents

Status: Active
Applies to: proposals, final reports, and invoices drafted for freelance clients
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner's proposal_generator (templates/proposal.html, final_project_report.html, invoice.html structure; reference/proposal_visual_spec.md); owner Working Standard Profile sections 3 and 12

Section titles below follow the owner's templates. Values and narrative follow the
client's language. Fixed wording comes from the template, never from the agent.

## Proposal

| Section | Content |
| --- | --- |
| Header | engagement title, client, developer, fee `[OWNER]`, estimated timeline |
| 1. Project Summary | objective in a few sentences, faithful to the brief |
| 2. Project Scope | agreed in-scope items, each testable at acceptance |
| 3. Out of Scope | explicit non-goals, including likely assumptions the client may hold |
| 4. Development Timeline | milestones with checkpoints; each milestone names what the client reviews |
| 5. Fees and Payment | total, down payment, final payment, notes: all `[OWNER]` until supplied |
| 6. Prerequisites | access, content, accounts, and decisions the client must provide, with dates |
| 7. Deliverables | handover items per the `release-delivery` handover standard |
| 8. Terms and Conditions | template-owned (acceptance window, payment deadline, warranty, portfolio permission) |
| 9. Agreement | signature table for client and developer |

Before presenting a draft, list for the owner: confirmed facts, applied defaults,
placeholders, and assumptions the proposal relies on.

## Final report

| Section | Content |
| --- | --- |
| Header | engagement number, document number, start, completion, and finalization dates, status |
| 1. Project Summary | what was delivered |
| 2. Proposal Overview | approved proposal number, approval date, approver, engagement value |
| 3. Project Scope | each approved scope item with result and status |
| 4. Out of Scope | as approved |
| 5. Scope Adjustment | original item and the approved revision |
| 6. Added Scope | approved added items with description and price `[OWNER]` |
| 7. Development Logs | dated log entries |
| 8. Deliverables | handover items and where each was delivered |
| 9. Terms and Conditions | template-owned |
| 10. Agreement | signature table |

Every row cites a record: an approved scope item, an approved scope change, or a log
entry. Nothing appears that has no record.

## Invoice

| Section | Content |
| --- | --- |
| Header | invoice number, billed to, issued by, invoice date, due date, payment status |
| 1. Payment Details | items with amounts; added-scope items name their scope change |
| 2. Payment History | recorded payments and their status |
| 3. Payment Instructions | owner's configured bank details, read from configuration only |
| 4. Fees and Payment | template-owned payment terms |

## Checks before the owner sends anything

- Every number and date matches its record; totals recompute.
- No placeholder remains, and no internal version, note, or evidence link leaks into
  the client copy.
- The document is the exact version the owner reviewed (HTML and PDF from the same
  version when both exist).
