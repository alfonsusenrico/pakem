---
name: freelance-delivery
description: Use when running a freelance engagement from first contact to closure - turning raw client notes into a brief, drafting a proposal, recording an approval or scope change, planning development from an approved proposal, drafting a final report or invoice, recording payment, handing over, or closing a project.
---

# Freelance Delivery

An engagement moves through fixed stages, each closed by an owner decision. Agents
prepare evidence and drafts; the owner approves, signs, sends, and records money.
The framework's `change-planning`, `release-delivery`, and `state-and-handoff`
skills apply inside the development stage.

## 1. Authority

| Agents may | Only the owner may |
| --- | --- |
| Structure raw notes into a brief and list missing facts | Talk to the client or send any document |
| Draft proposals, final reports, invoices, and change records | Set fees, payment terms, dates, warranty, and portfolio permission |
| Prepare an approval, payment, or handover record from evidence the owner supplies | Confirm that the client approved, paid, or received the handover |
| Propose stage transitions with the guard evidence | Perform a stage transition, reopen a closed engagement |

Never fill an unknown commercial, legal, approval, or payment fact; write
`[OWNER: <fact>]` and list it as a question. Never calculate or reuse a document
number by hand when a numbering service exists.

## 2. Stages

`discovery` -> `proposal` (drafts and revisions) -> `proposal approved` ->
`development` -> `final report` (drafts and reviews) -> `final report approved` ->
`invoiced` -> `paid` -> `closed`, with the handover recorded before closure. Follow
[lifecycle](references/lifecycle.md) for each stage's work, owner gate, forbidden
actions, and exit guard.

## 3. Records

Follow [records](references/records.md):

- An approval is a separate record that names the exact document version, the
  approver, the method, and the evidence. It is never a status flag on the draft.
- An approved proposal or final report is immutable. A revision before approval is a
  new internal version under the same public document number.
- A change after proposal approval is a scope-change record (category, status, price
  and timeline impact, evidence); the approved proposal never changes.
- The final report is built from the latest approved proposal, the scope changes the
  owner marks as included, and the development log.
- Closure requires an approved final report, every issued invoice settled or voided
  with payment evidence, and a recorded handover.

Document numbers come from one allocator and are never reused; record fields follow
the proposal generator's contract (records reference).

## 4. Documents

Draft documents with the section structures in [documents](references/documents.md).
Fixed wording (terms and conditions, payment confirmation, section titles) belongs
to the owner's template; populate fields only. Client-facing values and narrative
follow the client's language.

## 5. Development

Treat the approved proposal as the contract for planning:

1. Record the approved scope, non-goals, milestones, and acceptance criteria in the
   repository's planning documents (`change-planning`): one phased plan, then an
   OpenSpec change per milestone. Pricing and payment data never enter the
   repository.
2. Plan nothing beyond the approved scope. Work requested outside it waits for an
   approved scope-change record; then plan it as its own OpenSpec change that names
   the record.
3. Keep a dated development log (what was delivered, blockers, evidence) from
   `verification.md` files and merged changes; it feeds the final report.
4. Release and hand over through `release-delivery`; working inside the client's
   own repository or team follows `client-engagement`.

## 6. Where records live

- Commercial records (brief, proposal, approvals, scope changes, final report,
  invoices, payments, handover) live outside the code repository: in the proposal
  generator or a private engagement folder the owner names.
- The repository's private `research/notes/` may hold the technical brief and
  meeting notes; never commit prices, bank details, or payment evidence there.
- `PROJECT_STATE.md` tracks the current stage and the next owner gate.

## 7. Proposal generator (optional)

The owner's `proposal_generator` project implements this lifecycle with stage
guards, numbering, immutable HTML and PDF artifacts, and approval, scope-change,
payment, and handover records. When the owner uses it, call its tools under its own
contract (`skill/freelance-document-workflow/` in that repository) and do not
duplicate its records by hand. Without it, keep the same records as Markdown in the
engagement folder.
