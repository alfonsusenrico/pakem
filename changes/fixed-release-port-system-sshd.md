- The release port must be held by this host's own ssh service (its main process, or
  systemd when sshd is socket-activated): a second sshd on that port, such as a Git
  server's or a container's, is refused by pid, because it has its own settings and never
  the deploy user's forced command (found on the owner's server, where another sshd held
  2222 on the Tailscale address).
