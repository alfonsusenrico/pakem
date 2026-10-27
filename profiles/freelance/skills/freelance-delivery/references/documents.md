# Engagement Documents

Status: Active
Applies to: every document `pakem docs` writes for a freelance engagement
Last reviewed: 2026-10-27
Review by: 2027-04-27
Sources: owner decisions 2026-10-27 (framework ADR 0014: catalogue, layout D, wording); owner Working Standard Profile sections 3 and 12

Every document is a short approval document in layout D: the classic header (issuer and
code in the running header, centred title) over ruled content, A4, in the engagement's
language. `pakem docs new <type>` writes the fields to fill. Fixed wording comes from the
store's `wording/<type>.<lang>.md` (`## <section>` blocks, `{{fact}}` placeholders filled
from `engagement.toml` and the document), never from the agent.

## The catalogue

| Type (`pakem docs new`) | Code | Content the agent fills | Fixed wording sections |
| --- | --- | --- | --- |
| `proposal` | PROP | date, validity, timeline, summary, scope, out of scope, assumptions, prerequisites, fee, milestone rows (name, delivered, date, payment), deliverables | `acceptance`, `terms`, `agreement` |
| `quotation` (small jobs) | QUO | date, validity, work, item rows (item, qty, price), optional terms | `note` |
| `brief` | PRJ | date, source, background, needs, open questions | `note` |
| `nda` | NDA | date, purpose, term | `clauses` |
| `agreement` (only when asked) | AGR | date, client representative | `clauses` |
| `change` | CR-NN | date, requested by, category, change, cost, timeline impact, other scope | `note` |
| `milestone` (with UAT) | MSR-NN | milestone, test window, environment, scenario rows | `note` |
| `minutes` | MOM-NN | date, place, attendees, agenda, decision rows | none |
| `invoice` | INV-NN | date, due date, item rows (description, amount) | `payment_note`, `payment_terms` |
| `receipt` | KW-NN | invoice, date, method, amount, description | `statement` |
| `final` | FINAL | period, summary, result rows, adjustments, added-scope rows, log rows, deliverable rows | `terms`, `agreement` |
| `bast` (per engagement) | BAST | date, client representative and title, item rows | `statement` |
| `warranty` | WAR | date, reporting channel, response rows | `terms` |
| `workreport` | WR-NN | role, period, entry rows (date, work, hours) | `note` |

## The proposal's sections

1. Project Summary 2. Project Scope 3. Out of Scope 4. Assumptions 5. Client
Prerequisites 6. Timeline and Payments (milestone table, payments adding up to the fee)
7. Acceptance (UAT) 8. Deliverables 9. Terms and Conditions 10. Agreement, with the
signature table. The total fee sits in the facts at the top.

## Before the owner sends anything

- `pakem docs check` passes: no `[FILL]` or `[OWNER: ...]` left, totals recompute,
  referenced documents exist and are approved where they must be.
- The PDF is the version the owner reviewed. A later change is a new version
  (`pakem docs revise`), or after approval a change request.
- A receipt above Rp5,000,000 carries a Rp10,000 *meterai* (the box prints
  automatically). Agreements and the NDA carry one too.
