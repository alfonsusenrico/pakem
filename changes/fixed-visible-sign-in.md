- Commands that sign in to a server with the owner's login (`setup host`, `setup check
  --host`, `host runner`, `host allow-device`, `host revoke-device`) open the connection
  in the foreground first, so whatever the host asks before the login shows: a password, a
  new host key, or Tailscale SSH's check link, which used to be hidden while the command
  seemed to hang.
