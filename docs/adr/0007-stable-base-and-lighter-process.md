# 0007. Stable base and lighter process

- Status: Accepted
- Date: 2026-10-13

## Context

After the 1.0.0 rules audit, four days went into 27 commits, 8 pull requests, and four
releases, most of them chasing one rule on one host through a live eval harness whose
output checks are word heuristics; the conclusion was that behaviour was fine and the
grader was wrong. Every change to the pinned kernel digest also meant syncing seven
repositories, two of them through pull requests that deploy production. The owner
works alone on many projects and wants the framework to reduce work, not add it.

## Decision

1. The framework is a stable base. Rules change only for a problem a real project hit,
   in batches of at most one release a month, one pull request per batch, and no
   release for wording alone. No new `pakem` mechanism unless a project needs one.
2. Compliance evals are optional: run by hand after a large host or model change,
   never as a release gate, and read as evidence rather than a score to tune.
3. Repositories no longer pin the kernel digest (supersedes ADR 0003). The kernel is
   installed globally for every first-class host and verified to load, so the copy only
   added sync work. `pakem project sync` removes the old block and keeps profile blocks,
   which remain the only way organization and freelance rules reach a repository.
4. ADRs only for decisions with real trade-offs. Versioning: MAJOR only when a floor
   changes or an existing install or project would break; MINOR for new or changed
   rules; PATCH for fixes.

## Consequences

- Kernel changes reach every project through `pakem install` alone; repositories are
  synced only when a profile digest changes.
- A host that fails to load the global kernel would now see no framework rules in a
  repository; `pakem doctor` and the host load checks in `hosts/targets.toml` cover this.
- Less measurement means weaker evidence on per-host adherence; the eval harness stays
  available when a change warrants it.
