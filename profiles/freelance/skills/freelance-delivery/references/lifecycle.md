# Engagement Lifecycle

Status: Active
Applies to: freelance engagements from first contact to closure
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: owner's proposal_generator (backend/app/domain/workflow.py; skill/freelance-document-workflow/workflow_stages.md, tool_contract.md, system_prompt.md); owner Working Standard Profile sections 3 and 12

## Sequence

`discovery` -> `proposal` -> `proposal approved` -> `development` ->
`final report` -> `final report approved` -> `invoiced` -> `paid` -> `closed`

- A proposal may be revised zero or more times; a first draft may be approved
  directly. The same holds for the final report.
- The handover may be recorded once the final report is approved and must be
  recorded before closure.
- Moving backwards is not a transition: a change after approval is a scope-change
  record, and reopening a closed engagement needs an explicit owner instruction and a
  recorded reason.

## Stages

| Stage | Agent work | Forbidden | Exit guard (owner decides) |
| --- | --- | --- | --- |
| `discovery` | Keep raw notes verbatim with their source; structure them into a brief; list missing facts as grouped questions; run the `change-planning` interview and constraint fit (budget, deadline, maintainer, hosting) | Drafting an official document from unconfirmed facts; quoting a price | Every fact the proposal needs is confirmed or supplied by the owner |
| `proposal` | Draft from the brief; revise as new internal versions; flag every `[OWNER: ...]` placeholder | Approving on the client's behalf; editing an approved version | An approval record names the exact version, approver, method, and evidence |
| `proposal approved` | Read the approved snapshot; prepare the development plan | Changing scope, price, or timeline in the approved document | Owner starts development |
| `development` | Plan and build per the approved scope (`change-planning`); keep the development log; draft scope-change records for requests | New proposal versions; a final invoice | Owner declares the delivery ready for finalization |
| `final report` | Draft from the latest approved proposal, included scope changes, and the log; revise as new versions after review | Using an unapproved proposal as source; invoicing as final billing | An approval record for the exact report version |
| `final report approved` | Draft the invoice(s); prepare the handover package (`release-delivery`, `references/handover.md`) | Editing the approved report | Owner issues the invoice |
| `invoiced` | Draft further invoices only for approved added scope; prepare payment records from evidence the owner supplies | Marking paid without payment evidence | Every issued invoice is settled or voided |
| `paid` | Prepare the handover and closure records | A new invoice for a settled balance without an approved added scope | Final report approved, payments settled, handover recorded |
| `closed` | Read and export records; start a new engagement or change request on owner instruction | Altering any historical document, approval, or payment | Terminal |

## Owner gates to state in PROJECT_STATE.md

Record the current stage and the next gate as one line, for example
`Stage: final report (v2 sent for review); next gate: client approval evidence`.
