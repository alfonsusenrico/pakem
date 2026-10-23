- The deploy user's SSH keys live in a root-owned `/etc/af/authorized_keys`, which sshd
  reads for `af` alone (`AuthorizedKeysFile`, via `/etc/ssh/sshd_config.d/pakem-user.conf`);
  the user could rewrite its own `~/.ssh` files, or add `authorized_keys2`. The bootstrap
  moves the existing lines once, keeps the change only when sshd confirms it, and `check`
  reports it. Which devices exist is read from `allowed_signers`.
- `pakem host allow-device --device builder-<project> --key FILE` gives a shared builder its
  own key for one project (ADR 0010 decision 8).
- The deployment contracts note that the release port listens wherever sshd does, so a
  host with a public or LAN address firewalls it.
- Run `pakem setup host <name>` to update each host.
