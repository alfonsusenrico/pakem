- `release-delivery`: each project keeps its release protocol in a `## Release` section
  of its `AGENTS.md` (ADR 0013). The agent learns it from the repository on the first
  release, or interviews the owner when the method is undecided, records it, and follows
  it after. The owner's go covers the whole protocol, and the agent stops only for a
  merge and on failure. New reference `references/release-protocol.md`.
