- `pakem project init` starts a GitHub project from its fixed facts (visibility, release
  kind, delivery, environments, stack, capabilities): `AGENTS.md`, `.pakem/project.toml`,
  CI for the forge and runner, deploy files and starters for a service we release,
  OpenSpec, `research/`, the stack's tooling and dev tools, the git hooks, the bootstrap
  commit, and a private (or `--public`) GitHub repository with `main` pushed.
- `pakem project adopt` brings an existing repository to the standard on
  `chore/adopt-pakem`: it writes only missing files and lists every difference as a
  finding.
- `pakem decide` records a gap as answered, provisional, or pending in
  `.pakem/decisions.toml` and regenerates the Open decisions list in `AGENTS.md`;
  `pakem project check --gaps` lists each gap with its state and whether its target files
  prove it; `pakem status` raises due decisions; `pakem release` waits for decisions that
  block a release. `pakem project check` warns about starter stubs.
