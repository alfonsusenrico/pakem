- `af_runner.sh` (installed in `/opt/af` by the bootstrap): a free, isolated self-hosted
  GitHub Actions runner for private repositories. `install` creates `ci-runner` with its
  own rootless Docker (from the host Docker's own source), caps its slice, and pins the
  runner by checksum; `add-repo` and `remove-repo` take tokens on standard input only;
  `check` proves the isolation. Opt-in sandbox test: `AF_SANDBOX_RUNNER=1`.
- The GitHub release template takes `multi_architecture` (default true): false skips the
  arm64 emulation and BuildKit container, so host-architecture releases build on a rootless
  self-hosted runner.
