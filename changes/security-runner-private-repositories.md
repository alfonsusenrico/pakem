- The self-hosted CI runner serves private repositories only, as GitHub reports them:
  `pakem host runner add` and `af_runner.sh add-repo` refuse a public repository, whose
  pull requests could run any fork's code on the server, and `af_runner.sh check` fails
  for a runner registered for one. `pakem project adopt` reads the visibility from GitHub
  instead of a flag or a prompt, and `pakem project check` exits 6 when a public
  repository has self-hosted jobs.
