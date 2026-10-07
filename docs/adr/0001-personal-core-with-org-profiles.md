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
repository (`agent-framework`). Organization- or client-specific platform how-tos
live in separate profile repositories owned by that organization, which declare
the framework versions they support. The core never contains organization content.

## Consequences

The core can follow the owner across employers and clients. Each profile needs its
own repository, version, and owner. Profiles plug in through `profile.toml`.
