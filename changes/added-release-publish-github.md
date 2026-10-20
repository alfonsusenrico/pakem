- `release-delivery` CI (contracts v1, draft): `af_publish.py` builds every image once
  (multi-architecture, revision label), packs a reproducible deploy bundle, signs every
  digest with the project key (cosign 3, no public log), and moves `release-staging`;
  `promote` turns the verified staging record into the production record without
  rebuilding; `rollback` re-points an environment. GitHub reusable workflow `release.yml`
  and `release.example.yml` (main publishes, `v*` tags promote, manual rollback), with
  cosign, oras, binfmt, and BuildKit pinned in `PINS.md`. Opt-in sandbox test publishes,
  promotes, and rolls back against a real registry.
- The deploy asset scan guard in `tests/test_ci_templates.py` is a lower bound instead of an
  exact count, so new engine files no longer conflict.
