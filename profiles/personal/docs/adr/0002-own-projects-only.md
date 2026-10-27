# 0002. The personal profile covers the owner's own projects only

- Status: Proposed
- Date: 2026-10-27

## Context

ADR 0001 made this profile hold the owner's freelance practice, and the owner's own
projects pinned no profile. Framework ADR 0012 found that this left the globally
installed kernel as the only home for rules meant for the owner's own projects, which
then reached work and client repositories too (`default:no-billed`).

## Decision

- This profile holds the rules for the owner's own projects, from the owner's answers on
  2026-10-27: free first weighed against quality (`default:free-first`), paid options
  proposed with their cost and approved by the owner; AI calls through the owner's
  inference gateway by default; GitHub, private by default, with hosted runners on public
  repositories and the self-hosted runner on private ones; hosts and environments per
  project; English for code, docs, and commits; the owner's personal email; the owner
  reviews and merges.
- Every personal project pins it. The workspace map stays here.
- The freelance lifecycle and client-engagement rules move to `profiles/freelance`.

## Consequences

- MAJOR version (1.0.0): the digest is replaced, and the two skills leave.
- Personal projects sync this profile. A project's `default:no-billed` bullet becomes a
  `default:free-first` bullet.
- Devices install `profiles/freelance` beside this profile to keep the freelance skills.
