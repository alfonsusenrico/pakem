- `pakem setup host <ssh-target>`: uploads the deploy assets (merged `main` or a release
  only) over one SSH connection, runs the bootstrap with sudo, optionally the CI runner
  (`--runner`), removes the upload, and records the host in `~/.config/pakem/hosts.toml`.
  It signs in with the owner's own login and runs only on the owner's terminal;
  without one it reports the exact command as an owner action.
- `pakem setup check --host <name>`: the bootstrap's check (and the runner's) with the
  recorded options.
- `pakem host runner add|remove <host> <owner/repo>`: registers a repository with the
  host's runner, with a token from the owner's `gh` login that is never printed or
  passed as an argument.
