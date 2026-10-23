- Client and other private names live in a device-local list,
  `~/.config/pakem/private-names.txt`, never in the public tree. `pakem lint` fails on
  them in the framework, `pakem propose` refuses them, and `pakem project check` warns,
  in a public repository, about each tracked line that names one or a host recorded in
  `hosts.toml` (values never printed). The personal workspace map uses generic wording
  for client folders. The `pakem` skill tells agents to write "the server" and "the
  release port" in public repositories.
