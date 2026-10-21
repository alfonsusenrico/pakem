Personal and freelance rules (Pakem profile `personal`):

- Commit with the owner's own name and email, set repo-locally before the first
  commit. A client-issued identity needs an `## Overrides` entry.
- In a client repository, exclude `AGENTS.md`, `PROJECT_STATE.md`, `JOURNAL.md`,
  `openspec/`, `research/`, and `.githooks/` locally (`project-bootstrap`) and
  confirm `git status` shows none of them before every push.
- Follow the client's conventions in their repository; record each one that departs
  from an owner default as an `## Overrides` bullet with the client rule as reason.
- In a client repository, once the owner accepts the work, push the topic branch and
  report the compare link; the owner opens the merge request (`default:mr-policy`).
- Client servers, shared databases, client data, and client accounts need the
  owner's explicit instruction for that task. Draft messages to the client or the
  client's team for the owner; never send them.
- Never invent prices, dates, approvals, or payments; agents draft commercial
  documents and the owner approves and sends them.
- Deliver to the `release-delivery` handover standard, in the client's language.
- Load `freelance-delivery` for the engagement lifecycle (first contact to closure)
  and `client-engagement` when working inside a client's or partner team's
  repository.
