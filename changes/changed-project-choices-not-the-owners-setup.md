- Where a project's CI runs is its own recorded choice, `ci_runner` in `.pakem/project.toml`:
  hosted (the forge's runners) or self-hosted (a Pakem runner). `pakem project init` and
  `adopt` ask it for a private repository, defaulting to self-hosted once the device knows a
  host with a runner and to hosted otherwise (`--ci-runner`). Public repositories always use
  hosted runners, and Pakem refuses self-hosted for them. Facts recorded before the choice
  keep their CI as it was. Before this, every private repository got the self-hosted runner,
  whose jobs never start without a Pakem server.
- `pakem release` publishes origin's default branch (`main`, `master`, `trunk`, ...) instead
  of always `origin/main`.
- The reusable deploy workflow no longer joins a tailnet unless the caller passes
  `tailscale: true`. The GitLab templates' notes explain their portability choices instead
  of naming one organization's instance. The `pakem` skill marks the release, project, and
  proposal areas GitHub only, and `project init` and `adopt` name the GitLab templates when
  they refuse `--forge gitlab`. The release-delivery skill no longer says tags run checks.
- Projects: a project that calls Pakem's reusable `deploy.yml` and reaches its host through
  a tailnet passes `tailscale: true` in its caller.
