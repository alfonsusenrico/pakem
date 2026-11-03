- CI templates run faster on the self-hosted runner and are safe to run in parallel there.
  `security.yml` is one `security` job instead of four (`secret-scan`, `python-audit`,
  `node-audit`, `ci-lint`), and every check still runs after another fails. Its verified
  tools stay in the runner's tool cache, named by checksum (about 20 seconds per run).
  `database.yml` binds Postgres to a random loopback port instead of `5432` on every
  interface, and passes it through `TEST_DATABASE_URL` and `DATABASE_URL`. `verify-node.yml`
  and `database.yml` use the Actions npm cache only on GitHub-hosted runners. The CI that
  `pakem project init` writes checks out only for pull requests in its `changes` job, and
  runs OpenSpec through `npm exec`, cached between runs. Projects that copied these
  workflows re-copy them; checks named `security / secret-scan` and the like become
  `security / security`.
