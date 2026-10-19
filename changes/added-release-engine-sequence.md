- `release-delivery` engine (contracts v1, draft): `af_engine.py` runs the deployment
  sequence under a lock (verify, pull by digest, settings, preflight, drain, backup,
  migrate, start, revision health check, hooks, promote, retention with image cleanup,
  deployment record) and on failure restores the previous release only when the schema is
  unchanged, otherwise leaves services stopped (exit 5); `deploy`, `status`, and
  `secret-set` commands with a private host configuration.
- Contracts: `health.port_setting` names the placement value holding the loopback port the
  engine checks.
- Opt-in end-to-end test (`AF_SANDBOX=1`) deploys real images through a real registry and
  cosign: install, upgrade with a backup, and automatic restore of a broken release.
