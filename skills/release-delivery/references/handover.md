# Client Handover

Status: Active
Applies to: work delivered to clients
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner client deliveries (2026); owner Working Standard Profile section 12

## Always included
- **Runbook** in the client's language: requirements, install, configuration
  (variable names and how to obtain each secret - never the values), deploy, operate
  (scheduled jobs, logs, health), backup and restore, rollback, troubleshooting.
- **User or content-editor guide** in the client's language for daily tasks.
- **API and integration documentation**: generated OpenAPI reference plus integration
  notes.
- **Deployment package** when the client operates hosting: artifact built from a tagged
  commit, runbook, preconditions check (read-only SQL), seed and seed-cleanup scripts,
  and a release note.

## Kept private
`AGENTS.md`, `PROJECT_STATE.md`, `JOURNAL.md`, `openspec/`, and `research/` stay out of
the delivered repository or archive (excluded locally or kept in a private remote),
unless the client buys the process.

## Credentials
Transfer credentials only through the client's chosen secure channel, never in
documents, chat, or the repository. Rotate any credential used during development
before handover.
