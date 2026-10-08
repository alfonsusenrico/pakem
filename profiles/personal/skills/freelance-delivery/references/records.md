# Engagement Records

Status: Active
Applies to: briefs, approvals, scope changes, logs, invoices, payments, handovers, and closures of freelance engagements
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner's proposal_generator (skill/freelance-document-workflow/field_schema.md, workflow_stages.md, system_prompt.md; backend/app/services/document_numbering.py)

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

| Record | Required fields |
| --- | --- |
| Brief | source notes (verbatim, dated), objective, users, scope candidates, out of scope, constraints, client prerequisites, deliverables, open questions, evidence level per fact |
| Approval | document type, document number, internal version, approver name and role, approved at, method (signature, email, verified chat), evidence reference, conditions |
| Scope change | id, title, description, category (`revision`, `added_scope`, `adjustment`, `out_of_scope`), status (`requested`, `approved`, `implemented`, `rejected`, `cancelled`), price impact, timeline impact, requested/approved/implemented dates, evidence reference |
| Development log entry | date, summary, deliverables completed, blockers, evidence (commit, merge request, `verification.md`) |
| Invoice | document number, items (each added-scope item names its approved scope change), issued at, due at, total, status, linked final-report approval |
| Payment | invoice, amount, paid at, method, reference, evidence reference, status (partial or settled) |
| Handover | handed over at, items (per the `release-delivery` handover standard), recipient, delivery method, evidence reference |
| Closure | closed at, guard evidence (final-report approval, settled invoices, handover), warranty start and end when agreed |

## Immutability

- A document version is immutable once generated; a revision creates the next
  internal version. The public document number never carries the version.
- An approval locks the version it names. Later changes become scope-change records.
- A scope change counts in the final report only when its status and evidence meet
  the owner's instruction.
- Payment and approval records are corrected by a new record that references the
  old one, never by editing it.

## Numbering scheme (optional)

| Item | Format | Rule |
| --- | --- | --- |
| Engagement | `PRJ-YYYY-XXX` | `XXX` is the engagement's sequence within its start year; assigned once |
| Proposal | `PROP-YYYY-XXX` | Same year and sequence as the engagement |
| Final report | `FINAL-YYYY-XXX` | Same year and sequence as the engagement |
| Invoice | `INV-YYYY-XXX-NN` | `NN` counts invoices within the engagement from `01` |

Numbers come from one allocator (the proposal generator, or a ledger the owner
keeps) and are never reused, even for a voided invoice.
