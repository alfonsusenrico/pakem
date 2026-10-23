- Gap checks no longer pass on the starter files alone. `health.proof` needs its
  `[health] path` served by the project's own code (not a starter stub) and a real
  `docs/deployment.md`. `settings.sources` fails while `deploy/settings.toml` is still the
  template. `migrations.rule` and `gateway.profile` gain checks (`AGENTS.md` states the
  rule, or names the gateway with a `default:no-billed` approval). A bulleted
  `- Language:` line now counts. The gap protocol tells agents to read the code an answer
  depends on, such as the health handler.
