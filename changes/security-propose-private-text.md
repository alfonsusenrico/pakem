- `pakem propose new` and `revise` refuse (exit 6) a proposal whose text or added lines hold
  a private name, a host, address, or port from `hosts.toml`, a non-documentation IP
  address, or a home directory path, naming each place but never the value. They print the
  text and ask before pushing (`--yes` skips the question, after the owner saw it). The
  project is "a project" unless `--project` names it. Subjects are `docs:` (`feat:` for
  tooling, or `--type`), cut at a word within 72 characters.
