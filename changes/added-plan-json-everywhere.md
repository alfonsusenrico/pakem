- `--plan` and `--json` on every command: `setup key` and `host add` gain `--plan`; with
  `--json`, `setup key`, `setup host`, `setup check --host`, and the `host` commands print
  one result document on stdout (`{status, done, owner_actions, gaps, checks}`, plus
  `error`) and move what they print as they go, ssh and sudo included, to stderr.
