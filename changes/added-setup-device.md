- `pakem setup device`: installs git, `gh`, `glab`, uv, and the docker CLI with Homebrew
  (macOS) or apt (Debian and Ubuntu) after one confirmation; keeps a running container
  runtime or starts Colima; creates the device key `~/.ssh/pakem_device_ed25519` (connects
  and signs releases, never the login key); writes `~/.config/pakem/device.toml`; installs
  the framework if missing. Forge logins and what the package manager cannot provide are
  reported as owner actions (exit `3`).
- `pakem setup check`: lists tools, versions, the runtime, the key, and forge logins
  without changing anything.
