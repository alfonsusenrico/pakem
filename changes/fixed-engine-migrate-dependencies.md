- The release engine runs the one-off migration with its Compose dependencies (the
  database) instead of `--no-deps`, so a project's first deployment can migrate; the
  configuration preflight still runs without dependencies.
