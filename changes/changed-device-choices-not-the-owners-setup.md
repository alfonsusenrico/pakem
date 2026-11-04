- A device prepares and checks only what it works with. `pakem config set forges
  github|gitlab|both` (default `github`) and `containers yes|no` (default `yes`) decide what
  `pakem setup device` installs and `pakem setup check` checks: a GitHub-only device is no
  longer asked to install `glab` and log in to GitLab, and one without containers is not
  asked for Docker. A device that uses GitLab too runs `pakem config set forges both`.
- `pakem install` without `--hosts` installs for the agent hosts found on the device
  (`detect` in `hosts/targets.toml`: Claude Code, Codex, Antigravity), or for every host
  when none is found yet, and names the ones it skipped. When it backs up a rules file of
  the user's own, it says where the file went and that `pakem uninstall` brings it back.
- `pakem config set update off` turns off the automatic update check, so no session start
  reaches the network; `pakem update` and `pakem status --refresh` still fetch.
- The README names `PAKEM_REPO` for installing a fork.
