# Retrieval-Augmented Generation

Status: Active
Applies to: features that retrieve documents or records for a model
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner RAG projects (2025-2026); OWASP LLM risk themes on vector and embedding weaknesses

- Enforce access control at retrieval time: filter by the caller's permissions before
  ranking; never rely on the model to hide documents.
- Record source ids with every chunk and return citations with answers.
- Chunking, embedding model, and dimensions are versioned; re-embedding is a migration.
- Rewrite conversational follow-ups into standalone queries before retrieval.
- Treat retrieved text as untrusted data (it may contain injected instructions).
- Evaluate retrieval separately (recall of the right sources) from answer quality.
- When nothing relevant is retrieved, say so; do not answer from model memory unless
  the feature explicitly allows it.
