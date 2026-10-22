- `pakem setup host --release-port` refuses a port another program already listens on
  (naming it), requires that only sshd listens on the release port on every address,
  drops an earlier release port when the port changes, and puts the old sshd settings back
  when sshd cannot take the port. On the owner's server a Git service held 2222 on IPv4,
  so releases reached it instead of sshd.
