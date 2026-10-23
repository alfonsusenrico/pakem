- `pakem setup host` stops with an owner action when the new device key could not be
  added to ssh-agent, no longer marks a host whose check failed as set up, removes its
  temporary directory on the server when an upload fails, and refuses an SSH target that
  starts with `-` (it would be read as an ssh option).
- `pakem setup key --no-passphrase` needs the owner's terminal: an agent never makes an
  unprotected key.
- `--json` stays parseable while installers run (their output goes to stderr). On macOS,
  an installed Docker Desktop or OrbStack that is not running is the owner's to start;
  pakem no longer installs Colima and switches the docker context.
- `af_runner.sh` passes runner tokens to `config.sh` through its environment
  (`ACTIONS_RUNNER_INPUT_TOKEN`) instead of an argument, so they never show in the
  process list.
