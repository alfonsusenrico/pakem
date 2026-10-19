- `release-delivery` engine (contracts v1, draft): `af_settings.py` builds an environment's
  runtime from committed defaults, host or CI placement, private secret files, and secrets
  generated once on the host; the runtime file carries `NAME_FILE` paths, never secret
  values, and every check names settings without printing values.
