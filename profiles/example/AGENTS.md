# example profile

A neutral example of a Pakem profile, for a small team. Copy it into your own repository
and replace every rule with your context (guide: `docs/profiles.md` in the framework).

## Rules for changing this profile

- Content here holds for every project the team runs: forge and CI, spending, review,
  language, and identity. Universal practice belongs in the framework core, and one
  project's facts in that project.
- Name no real person, company, host, or service account here.
- `digest.md` stays under 2,000 characters and must not repeat kernel lines.
- Check with `tools/pakem lint --profile profiles/example` from the framework root
  (0 errors) before any commit.
- Record releases in `CHANGELOG.md`, and bump `VERSION` and `profile.toml` together.
