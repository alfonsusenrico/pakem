# Engagement Records

Status: Active
Applies to: briefs, approvals, scope changes, logs, invoices, payments, handovers, and closures of freelance engagements
Last reviewed: 2026-10-27
Review by: 2027-04-27
Sources: framework ADR 0014 (`pakem docs`); rules audit 2026-10-12 (principles only)

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

Documents are version files in the `pakem docs` store. Owner facts (approvals,
payments, handover, voids) are entries in the engagement's append-only
`records.toml`, written only by `pakem docs approve|pay|handover|void`, each with its
method and evidence. The development log feeds the final report's `log` rows.

## Immutability

- A version is immutable once rendered (`pakem docs check` reports any change); a
  revision is the next version file. The public document number never carries the
  version.
- An approval locks the version it names. Later changes become scope-change records.
- A scope change counts in the final report only when its status and evidence meet
  the owner's instruction.
- Payment and approval records are corrected by a new record that references the
  old one, never by editing it.

## Numbering

`pakem docs` allocates every number once and never reuses it, even for a voided
invoice: `PRJ-YYYY-NNN` per engagement, every document sharing it (`PROP-2026-007`),
and a running suffix for repeating documents (`INV-2026-007-01`).
