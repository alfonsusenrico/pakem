# Engagement Lifecycle

Status: Active
Applies to: freelance engagements from first contact to closure
Last reviewed: 2026-10-27
Review by: 2027-04-27
Sources: owner decisions 2026-10-27 (framework ADR 0014); owner Working Standard Profile sections 3 and 12

`pakem docs` keeps the stage and refuses what a stage forbids. The owner decides every
gate; the agent records the owner's statement and evidence.

## Fixed scope

| Stage | Agent work | Gate to the next stage |
| --- | --- | --- |
| `discovery` | Keep raw notes with their source, write the brief, list missing facts as grouped questions, run the `change-planning` interview | Creating the proposal or quotation starts `proposal` |
| `proposal` | Draft from the brief, revise as new versions, flag every `[OWNER: ...]` | `pakem docs approve` on the offer, with the client's approval evidence |
| `development` | Plan and build the approved scope, keep the development log, draft change requests, milestone reports, and invoices per the milestone table | `pakem docs approve` on the final report |
| `finishing` | Final invoices, receipts, the BAST or handover, warranty terms | `pakem docs close`: final report approved (for a proposal), every invoice paid or void, handover recorded |
| `closed` | Read and export | Terminal. Reopening needs the owner's instruction and a recorded reason |

A quotation-based small job has no final report: closing then needs only the payments
and the handover.

## Workstream

`active` -> `closed`: work reports, invoices, receipts, and minutes. Closing needs every
invoice paid or void.

## Owner gates in PROJECT_STATE.md

Record the stage and the next gate as one line, for example
`Stage: development; next gate: client approval of MSR-2026-007-02`.
