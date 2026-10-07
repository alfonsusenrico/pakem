# LLM Security

Status: Active
Applies to: every LLM feature
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: OWASP Top 10 for LLM Applications 2026 (published 2026-08-03, https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/); OWASP Top 10 for Agentic Applications 2026 (https://genai.owasp.org); OWASP Agent Control Standard (announced 2026-09). Verify entry ids against the official documents before citing them.

Controls by risk theme:

| Risk theme | Controls |
| --- | --- |
| Prompt injection (direct and via documents or tools) | separate instructions from data; treat all retrieved and tool content as untrusted; never let model output trigger privileged actions without code-enforced checks |
| Sensitive information disclosure | no secrets or unnecessary personal data in prompts; output filtering for known sensitive fields; provider data-retention settings reviewed |
| Excessive agency | least-privilege tools; human confirmation for consequential actions; iteration and spend caps |
| Supply chain | pin SDK versions; review model and provider changes through evals; trusted model sources only |
| Data and model poisoning | provenance for eval and retrieval data; review ingestion sources |
| Unbounded consumption | token, cost, rate, and time limits per user and per request |
| Misinformation | grounding in supplied data; citations; deterministic facts; uncertainty shown honestly |
| Hidden context exposure (system prompts, memory) | never place secrets or access rules only in prompts; assume prompts can be extracted |
| Vector and embedding weaknesses | access filtering at retrieval; tenant separation in indexes |
| Improper output handling | treat output as untrusted: encode before rendering, validate before executing or storing |
| Agentic risks (goal hijack, tool misuse, identity and privilege abuse, memory poisoning, cascading failures, rogue agents) | scoped identities per agent, tool allowlists, memory hygiene, circuit breakers, audit of agent actions |
