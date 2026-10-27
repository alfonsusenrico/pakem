# 0001. Personal core with organization profiles

- Status: Accepted
- Date: 2026-10-07

## Context

The owner works as an AI engineer for an organization and also as a software
engineer, consultant, and freelancer. Earlier standards mixed universal practice
with one organization's platform (hub gateway, host application, release tooling)
and lived in an unversioned workspace owned by that work context.

## Decision

The universal kernel, skills, standards, templates, and tooling live in a personal
repository (Pakem, repository `pakem`). Organization- or client-specific platform how-tos
live in separate profile repositories owned by that organization, which declare
the framework versions they support. The core never contains organization content.

Amended 2026-10-08 (owner decision): the owner's own `personal` profile (personal
workspace map, freelance delivery lifecycle, client engagement rules) lives inside
this repository under `profiles/personal/`, because it travels with the owner like
the core. Organization and client profiles still live in separate repositories.

Amended by ADR 0012: the core also stays free of the owner's personal preferences. A
kernel rule must hold in personal, work, and client repositories alike, and
`profiles/personal` splits into `personal` and `freelance`.

## Consequences

The core can follow the owner across employers and clients. Each profile needs its
own repository, version, and owner. Profiles plug in through `profile.toml`.
