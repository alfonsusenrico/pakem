- Pakem commands prepare what they need on this device (`git`, `gh` or `glab`, uv, a
  container runtime, the device key, forge logins, the framework) as part of their own
  plan, approved by the owner's instruction in the session; owner-only steps (browser
  logins, sudo without a terminal, the key's passphrase) are reported with the exact
  instruction (exit `3`). Homebrew on macOS, apt on Debian, Ubuntu, and WSL; native
  Windows is refused with a pointer to WSL.
- `pakem setup device`: prepares everything at once for a fresh machine. `pakem setup
  check`: lists every need without changing anything.
- `pakem setup key [--no-passphrase]`: creates the device key `~/.ssh/pakem_device_ed25519`
  with a passphrase (asked by ssh-keygen on the owner's terminal) and adds it to
  ssh-agent, with the macOS Keychain where Apple's `ssh-add` is used.
