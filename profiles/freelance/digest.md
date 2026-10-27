Freelance rules (Pakem profile `freelance`), for client engagements:

- Before the first change, record the engagement in `AGENTS.md` (`client-engagement`),
  including whether client code and data may reach AI providers. Until that is
  recorded, stop and ask the owner.
- Whose account pays for a service, and what is approved, is settled per engagement and
  recorded there; there is no free-first preference.
- Commit with the owner's own name and email, set repo-locally before the first commit.
  A client-issued identity needs an `## Overrides` entry.
- Exclude `AGENTS.md`, `PROJECT_STATE.md`, `JOURNAL.md`, `openspec/`, `research/`, and
  `.githooks/` locally and confirm `git status` shows none of them before every push.
  Nothing the client receives shows agent use.
- Follow the client's conventions; record each departure from an owner default as an
  `## Overrides` bullet with the client rule as reason. Code is in English unless the
  client's convention differs; client-facing text uses the client's language.
- Client servers, data, and accounts need the owner's instruction for that task;
  releases and server setup also need an engagement that assigns them. Draft messages
  to the client; never send them.
- Never invent prices, dates, approvals, or payments; the owner approves and sends
  every commercial document. Load `freelance-delivery` for the engagement lifecycle.
