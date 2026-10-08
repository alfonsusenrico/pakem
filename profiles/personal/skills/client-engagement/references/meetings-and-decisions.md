# Meetings and Decisions

Status: Active
Applies to: meeting preparation, meeting outcomes, and decision logs in client engagements
Last reviewed: 2026-10-08
Review by: 2027-04-08
Sources: a freelance marketplace backend engagement (meeting prep, meeting outcomes, and phase-plan notes, 2026-09); owner Working Standard Profile section 3

All files live in the private `research/notes/` folder, dated
`YYYY-MM-DD-<topic>.md`. They are working notes for the owner, never sent as-is.

## Meeting brief

Prepare before each client or PM meeting:

```
# Meeting brief: <topic> (YYYY-MM-DD)

Evidence: <integration branch and commit = deployed revision>, <read-only checks run>, <build or URL inspected>

## 1. Status to report
| Item | State |   (merged and deployed / pushed, awaiting merge / done locally / in progress / not started)

## 2. Urgent issues (raise first)
1. <issue>: cause, impact, proposed fix, what we need from the client

## 3. Decisions needed
1. <question in the sources-of-truth question format>

## 4. Fixes on our side (no decision needed)
- <item>
```

State every status from evidence (commit, pipeline, probe), never from memory.

## Meeting outcomes

Right after the meeting, from the owner's notes:

- Keep the owner's raw notes verbatim in a section of their own.
- List each agenda item with its outcome: decided (by whom), deferred, or new work.
- Record the phase or page order agreed, if any.
- Mark each decision "confirmed" only when the owner says so.

## Decision log

One entry per confirmed decision, appended to a dated note or to the change it
governs:

- date, decision, decided by (role), source (meeting, message, document);
- the OpenSpec change that implements it, once created;
- what it supersedes.

Once a decision governs a change, it lives in that change's proposal or design; the
note keeps a pointer.
