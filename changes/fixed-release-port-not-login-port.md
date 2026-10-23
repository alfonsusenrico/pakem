- A release port can no longer lock the owner out: `pakem setup host` and `pakem host add`
  refuse a release port equal to the port the owner logs in on (`--port`, or what
  `ssh -G` gives the target), and `af_bootstrap.sh --release-port` refuses any port sshd
  already serves, other than the earlier release port. With `AllowUsers af` on such a
  port, only the deploy user could log in there, and recovery needed the console.
