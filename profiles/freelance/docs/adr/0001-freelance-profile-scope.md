# 0001. Freelance profile scope and location

- Status: Proposed
- Date: 2026-10-27

## Context

The owner's freelance practice (engagement lifecycle, work inside client teams, identity
in client work) lived in `profiles/personal` (its ADR 0001), together with rules for the
owner's own projects. Framework ADR 0012 splits the two: each project pins one context
profile, and freelance rules differ from personal ones (no free-first preference, and
facts settled per engagement).

## Decision

- The profile lives in the framework repository under `profiles/freelance/`, versioned on
  its own, because it belongs to the same owner and holds no client content.
- It holds the `freelance-delivery` and `client-engagement` skills, moved from
  `profiles/personal`, and a digest for client repositories.
- From the owner's answers on 2026-10-27: engagements are fixed-scope solo or a workstream
  in a client's team, with more kinds possible. Whose account pays, whether client code
  and data may reach AI providers, who merges, payment terms, repository home, issuer,
  tax, currency, signing, and handover acceptance are settled per engagement. Agents
  stop until the AI-provider answer is recorded. Agent use is not disclosed, so nothing
  the client receives shows it. Code is in English unless the client's convention
  differs, and client-facing text uses the client's language. Releases and server setup
  are the owner's only when the engagement assigns them.
- Client names, business rules, data, and prices never appear in the profile.

## Consequences

Client repositories sync this profile with
`pakem project sync <repo> --profile profiles/freelance`. Engagement documents and their
records move to a `pakem` documents area in a later change. Until then
`freelance-delivery` keeps its current records guidance.
