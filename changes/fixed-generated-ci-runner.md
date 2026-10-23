- CI written by `pakem project init` for a private repository now runs: it passed the
  self-hosted runner as the one string `[self-hosted, linux, x64, pakem]`, which no runner
  matches, so jobs waited forever; it passes the single label `pakem` now. The generated
  `database` job passes the template's required `test_cmd` and a `migrate_cmd` stub that
  fails until the project sets it (found linting generated CI with actionlint).
