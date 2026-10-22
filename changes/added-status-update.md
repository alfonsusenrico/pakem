- `pakem status`: whether this device runs the latest merged Pakem (a `main` checkout
  follows `origin/main`, a release checkout the newest release on `main`; network at
  most every 4 hours), and whether the project's profile pins match the installed ones.
- `pakem update`: moves the framework checkout to merged `main` or the newest release,
  re-runs the recorded `pakem install --apply` and `pakem enforce install`, and lists the
  files agents must re-read. Refuses unmerged or dirty checkouts.
- `pakem config set update notify|apply`: whether agents may run `pakem update`
  themselves (default `notify`).
- `pakem enforce install --host claude` adds a session-start hook (brief status) and a
  per-message hook (files to re-read after a mid-session install).
- `pakem install` records the installed commit and its options in the manifest, for
  `pakem status` and `pakem update`. Exit codes follow the command map (`3` owner action,
  `6` refused, `75` busy).
- `pakem help` lists each area's maturity stage.
