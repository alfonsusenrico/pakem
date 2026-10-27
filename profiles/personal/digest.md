Personal project rules (Pakem profile `personal`):

- Prefer free services and tiers, weighed against quality (`default:free-first`). When
  a paid option is clearly better, propose it with the free alternative, a monthly
  cost estimate, and the quality gained. Record an approved one as an `## Overrides`
  bullet naming the service and its budget.
- Call models through the owner's AI inference gateway. A project with only a simple
  AI call may call a provider directly and says so in `AGENTS.md`.
- Repositories live on GitHub, private unless the owner makes one public. Checks run on
  GitHub-hosted runners in public repositories and on the owner's self-hosted runner in
  private ones.
- Hosts and environments are chosen per project (`deploy/release.toml`), and services
  release with `pakem release`. Runtime settings and secrets live in env files on the
  host, never in the repository.
- Code, docs, and commits are in English; each project states the language of its UI
  and generated content.
- Commit with the owner's own name and personal email, set repo-locally before the
  first commit. The owner reviews and merges.
