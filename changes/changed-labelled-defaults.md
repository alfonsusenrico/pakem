- More of Pakem's opinions are labelled defaults that a project overrides in `## Overrides`
  instead of fixed rules: the prose rule against semicolon joins (`default:prose`), the
  `research/` folder (`default:research`), PostgreSQL as the database (`default:postgres`),
  hand-written migrations (`default:handwritten-migrations`; a project whose framework
  generates them reviews each file instead), and light and dark themes (`default:themes`).
  `pakem project check` accepts labels that core skills define, as well as the kernel's
  and the pinned profiles'. The backend skill's stack is a suggestion for a new service,
  not "the owner's backends". Change planning, the review checklist, and the capability
  standards cite `default:openspec`, `default:commits`, and `default:mr-policy` instead of
  restating them as unconditional, and a project that overrides `default:openspec` plans in
  its own format with the same stages and approval. Engineering identifiers follow the
  project's `Language:` line instead of always English.
