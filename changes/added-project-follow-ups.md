- Projects follow Pakem updates by themselves (ADR 0017). `pakem project check` warns about
  each file a project copied from Pakem (CI workflows, `deploy/af/*`) that is an older copy
  or differs from every Pakem version, and shows each `- Projects:` note of the change
  fragments merged since the commit in the project's `.pakem/framework.toml`.
  `pakem project sync --templates` re-copies older copies, and `--aligned` records the
  alignment. The session start hook adds a line while follow-ups wait, `pakem update` ends
  with the projects that have some, and the `pakem-update` skill works through them.
- Projects: once `pakem project check` shows no Pakem follow-ups you have not handled, run
  `pakem project sync --aligned --apply` and commit `.pakem/framework.toml`.
