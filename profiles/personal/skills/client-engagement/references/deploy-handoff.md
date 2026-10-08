# Deploy Handoff Package

Status: Active
Applies to: deployments built or run by a client or a teammate, not by the owner's pipeline
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: a freelance marketplace backend engagement (deploy handoff and phase-plan notes, 2026-09); framework release-delivery deployment and handover references; owner Working Standard Profile section 10

## When

The person who deploys is not the owner: a client operator, or a teammate who builds
the image and runs the deployment console. The agent prepares the package; the owner
hands it over.

## Contents

Build from a pushed commit on the integration branch, never from local or uncommitted
work. Name the package with its date and commit.

| Item | Rule |
| --- | --- |
| `README.md` | Commit and branch; what changed; the exact order of steps; who runs each step; what to report back |
| `00-preconditions.sql` | Read-only checks that must return the stated result (for example 0 rows) before deploying |
| Configuration | New or changed variable names, which are required or optional, and how to obtain each value; never values |
| Seeds | Numbered scripts, each in one transaction and idempotent, run in order after the deploy |
| `cleanup/` | A script that reverses each seed, for test or demo data |
| Verification | Commands or requests to run after the deploy, with expected results |
| Rollback | How to return to the previous revision, and whether a migration blocks it |

## Before handing over

- Run the preconditions read-only against the target database only with the owner's
  go-ahead, and record the result with the date.
- Confirm the migrations are recorded as expected before and after (count or names).
- When deploy timing matters (for example seeds that a still-running old build would
  undo), state it in the README as an ordering rule.
- Record the handoff, the package name, and the open follow-ups in
  `PROJECT_STATE.md`.

## After the deploy

On the owner's confirmation that the deploy and seeds ran: verify the live behaviour
against the product, record evidence tied to the deployed commit, then accept and
archive the OpenSpec changes.
