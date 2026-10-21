# 0005. Framework changes use ADRs, CHANGELOG, and semver

- Status: Accepted
- Date: 2026-10-07

## Context

The kernel makes native OpenSpec the default planning system for meaningful
project changes. OpenSpec models product behaviour as requirements and scenarios;
this repository is policy text plus a small tool, where the useful record is the
decision and its reason.

## Decision

Changes to this repository are recorded as ADRs (for decisions), CHANGELOG entries,
and semantic version tags, and are gated by `pakem lint` and the unit tests. The
repository `AGENTS.md` records this as an explicit override of `default:openspec`.
OpenSpec remains the default taught to projects.

## Consequences

Wording and rule changes stay lightweight. Revisit if the tooling grows into a
product with behaviour worth specifying.
