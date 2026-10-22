- `pakem setup host --release-port PORT` gives releases their own OpenSSH port, only for the
  deploy user and keys, for hosts whose port 22 is answered by Tailscale SSH (which never
  reads `authorized_keys`, so the device key's forced command did not apply and the key got
  a shell). `setup host` now checks with the device key that the receiver answers and names
  the fix when a shell does; status and history errors keep the host's message (found on
  the owner's server, Proof 1).
