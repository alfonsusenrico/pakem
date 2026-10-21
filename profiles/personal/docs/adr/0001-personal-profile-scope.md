# 0001. Personal profile scope and location

- Status: Accepted (owner, 2026-10-08)
- Date: 2026-10-08

## Context

Framework ADR 0001 keeps organization and client content out of the core and puts
organization platforms in separate profile repositories. The owner's freelance
practice (engagement lifecycle, work inside client teams, identity, workspace map)
is personal, applies only to personal and freelance repositories, and was planned in
the framework backlog as `profiles/personal`. Its evidence comes from the owner's
proposal generator and a freelance marketplace backend engagement.

## Decision

- The profile lives in the framework repository under `profiles/personal/`, versioned
  on its own (`VERSION`, `CHANGELOG.md`), because it belongs to the same owner and
  holds no organization or client content.
- It contains three things only: the `freelance-delivery` skill, the
  `client-engagement` skill, and the personal workspace map. Core rules on client
  privacy, local-only hooks, protected branches, and handover are referenced, not
  restated.
- Commercial records never enter code repositories. The proposal generator is an
  optional tool that implements the lifecycle; the skill works without it.
- Client names, business rules, data, and prices never appear in the profile.

## Consequences

Personal and freelance repositories sync the profile digest with
`pakem project sync <repo> --profile profiles/personal` (path from the framework root).
`pakem install --profile profiles/personal` checks the same git checkout as the core, so
the profile is installed from merged `main` together with it. Lessons from client engagements
reach this profile only as general patterns.
