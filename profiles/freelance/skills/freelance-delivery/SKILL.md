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
`[OWNER: <fact>]` and list it as a question (`pakem docs check` refuses to render
it). Never number a document by hand: `pakem docs new` allocates every number.

## 2. Stages

A fixed-scope engagement runs `discovery` -> `proposal` -> `development` ->
`finishing` -> `closed`; a workstream inside a client's team runs `active` ->
`closed`. `pakem docs` keeps the stage: an approved proposal or quotation opens
development, an approved final report starts finishing, and `pakem docs close`
checks the closing guards. Follow [lifecycle](references/lifecycle.md) for each
stage's work, owner gate, and guard.

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

`pakem docs` allocates every number, never reused, and keeps every record
(`approve`, `pay`, `handover`, `void`) in the engagement's append-only
`records.toml`.

## 4. Documents

Write every document with `pakem docs`, as in [documents](references/documents.md):
`pakem docs new <type> <PRJ>` writes the version file with every field to fill, the
agent fills it from the owner's answers, and `check` then `render` produce the PDF in
the approved layout. Fixed wording comes only from the store's `wording/` files,
which the owner approved; agents never edit them. Client-facing text follows the
engagement's language. The documents are short approval documents: keep them
simple.

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
  invoices, payments, handover) live in the `pakem docs` store: one private
  directory outside every repository, named in `~/.config/pakem/documents.toml`.
- The repository's private `research/notes/` may hold the technical brief and
  meeting notes; never commit prices, bank details, or payment evidence there.
- `PROJECT_STATE.md` tracks the current stage and the next owner gate.

## 7. A document, step by step

1. `pakem docs engagement new --client ... --name ... --kind fixed|workstream --lang
   id|en --currency IDR|USD` once per engagement, then fill `engagement.toml`
   (representative and the facts the wording needs) from the owner.
2. `pakem docs new <type> <PRJ>`, fill the version file, `pakem docs check <code>`
   until it passes, then `pakem docs render <code>`.
3. The owner reviews and sends the PDF. A change before approval is
   `pakem docs revise <code>` (a new version), never an edit of a rendered one.
4. Record what the owner reports, with its evidence: `pakem docs approve`, `pay`,
   `handover`, or `void`. `pakem docs status <PRJ>` shows what is left before closing.

When `pakem docs` lacks something a real engagement needs, report it and fix `pakem`;
never keep a parallel record by hand.
