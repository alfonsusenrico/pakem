# personal profile

The owner's personal and freelance profile for Pakem. It adds how the owner
runs freelance engagements and works in client repositories on top of the framework
kernel and skills; it never repeats or weakens them.

## Rules for changing this profile

- Content here is the owner's freelance practice: the engagement lifecycle, client
  engagement rules, identity, and the personal workspace map. Universal practice
  belongs in the framework core; organization platforms belong in their own profile
  repositories; project business rules belong in each project.
- Never write a client's name, business rule, data, price, or contact here. Describe
  engagements by role ("a freelance marketplace backend"); personal projects may be
  named.
- Reference core content instead of copying it: client-repository privacy and hooks
  (`project-bootstrap`), handover (`release-delivery`, `references/handover.md`),
  planning (`change-planning`).
- `digest.md` stays under 1,600 characters and must not repeat kernel lines; it is
  rendered into personal and freelance repositories' `AGENTS.md` by
  `pakem project sync <repo> --profile <this directory>`.
- `workspace/AGENTS.md` stays under 800 characters; it is rendered by hand to the
  personal workspace root, which keeps no other copy of these rules.
- Skills follow the framework's Agent Skills rules (trigger-first description of at
  most 350 characters, body under 500 lines, references one level deep with status
  headers). Skill names never collide with framework skills.
- Check with `tools/pakem lint --profile profiles/personal` from the framework root
  (0 errors) before any commit.
- Record decisions as ADRs in `docs/adr/`, releases in `CHANGELOG.md`, and bump
  `VERSION` and `profile.toml` together.

## Overrides

- `default:openspec`: profile changes use ADRs, CHANGELOG, and semver (framework
  ADR 0005).
