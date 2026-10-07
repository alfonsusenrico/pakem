# Agent Patterns

Status: Active
Applies to: features where a model selects and calls tools
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner tool-calling assistants (2026); OWASP Top 10 for Agentic Applications (2026)

- Tools have typed input and output schemas and return a uniform result shape
  (`ok` plus data, or a typed error) the model can act on.
- Business rules and permissions are enforced inside tools, never only in the
  prompt. The model cannot write fixed template text or bypass state transitions.
- Least privilege: each agent gets only the tools and scopes its task needs; no
  general shell or network tools without explicit design approval.
- Cap iterations, total tokens, and wall time; define what happens at the cap.
- Consequential actions go through a confirmation step (see the skill's section 3).
- Bound conversation memory by a token budget; summarize old turns into durable
  memory deterministically (low temperature) and keep summaries free of secrets.
- Log tool names, outcomes, and durations - not arguments containing personal data or
  content.
- Prefer a plain loop until you need durable state, checkpoints, resume, or
  human-in-the-loop interrupts; then adopt a graph framework.
