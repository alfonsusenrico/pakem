# personal profile

The owner's personal profile for Pakem: rules for the owner's own projects, on top of
the framework kernel and skills. It never repeats or weakens them.

## Rules for changing this profile

- Content here is how the owner runs their own projects: spending, AI calls, forge and
  CI, releases, language, identity, and the personal workspace map. Client engagements
  belong in `profiles/freelance`, universal practice in the framework core,
  organization platforms in their own profile repositories, and project business rules
  in each project.
- Personal projects may be named; never write a client's name, data, or price here.
- `digest.md` stays under 2,000 characters and must not repeat kernel lines; it is
  rendered into personal repositories' `AGENTS.md` by
  `pakem project sync <repo> --profile <this directory>`.
- `workspace/AGENTS.md` stays under 800 characters; it is rendered by hand to the
  personal workspace root, which keeps no other copy of these rules.
- Check with `tools/pakem lint --profile profiles/personal` from the framework root
  (0 errors) before any commit.
- Record decisions as ADRs in `docs/adr/`, releases in `CHANGELOG.md`, and bump
  `VERSION` and `profile.toml` together.

## Overrides

- `default:openspec`: profile changes use ADRs, CHANGELOG, and semver (framework
  ADR 0005).
