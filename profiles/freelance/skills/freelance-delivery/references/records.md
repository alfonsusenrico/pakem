# Engagement Records

Status: Active
Applies to: briefs, approvals, scope changes, logs, invoices, payments, handovers, and closures of freelance engagements
Last reviewed: 2026-10-12
Review by: 2027-04-08
Sources: owner's proposal_generator (skill/freelance-document-workflow/field_schema.md, workflow_stages.md, system_prompt.md); rules audit 2026-10-12 (principles only)

## Evidence levels

Tag every extracted fact that affects scope, money, approval, payment, or closure:

- **Confirmed**: explicit owner instruction, written client statement, signed or
  approved document, verified payment.
- **Supported**: stated in the supplied notes without conflict.
- **Uncertain**: ambiguous, contradictory, or inferred. Ask before it reaches a
  document.

A summary may shorten the source; it never adds deliverables, prices, dates, or
commitments.

## Field ownership

| Kind | Examples | Agent access |
| --- | --- | --- |
| Engagement facts | objective, scope, out of scope, milestones, client prerequisites, deliverables | Write from evidence or a documented default |
| Commercial facts | fee, down payment, payment terms, warranty, portfolio permission | Placeholder `[OWNER: ...]` until the owner supplies them |
| Calculated values | outstanding balance, down-payment percentage | Derive; never store as separate truth |
| Document snapshots | the values rendered into one version | Created with the version; never edited |
| Template content | terms and conditions, payment wording, section titles, layout | Read only |
| Configuration | owner defaults, bank details | Read only; change on owner instruction |

## Records

The record kinds are brief, approval, scope change, development log entry, invoice,
payment, handover, and closure. Their fields and statuses follow the proposal
generator's contract (`skill/freelance-document-workflow/field_schema.md` in that
repository); without it, keep them as Markdown in the engagement folder. Every record
that affects scope, money, approval, payment, or closure names its evidence.

## Immutability

- A document version is immutable once generated; a revision creates the next
  internal version. The public document number never carries the version.
- An approval locks the version it names. Later changes become scope-change records.
- A scope change counts in the final report only when its status and evidence meet
  the owner's instruction.
- Payment and approval records are corrected by a new record that references the
  old one, never by editing it.

## Numbering

Document numbers come from one allocator (the proposal generator, or a ledger the
owner keeps), are assigned once, and are never reused, even for a voided invoice. The
format belongs to that allocator.
