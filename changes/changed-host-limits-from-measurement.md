- Host limits come from the host itself or from the owner, never from one machine's numbers
  (ADR 0018). The release builder's CPUs and memory, its cache cap, the disk reserve a build
  needs, the CI runner's slice, its runners per repository, and its daily clean-up are
  derived on every run from the host's CPUs, memory, volume size and free space, and the
  measured size of recent builds. `pakem host limit <host> <key> <value>` sets one for a host
  instead (written to `/etc/pakem/host.toml` by `pakem setup host`), and an environment's
  `min_free_gb` still wins for its own builds. The builder's cache is pruned by size after
  every build, failed or not, and the builder is re-created when its limits change.
  `af-monitor` notifies `disk_low` (and `disk_recovered`) when free space falls below a level
  above the reserve, or when the trend reaches the reserve within 72 hours. Fixed values
  removed: builder 2 CPUs and 4 GB, cache kept 7 days, reserve 10 GB, runner slice at least 2
  CPUs and 4 GiB, runner cache 10 GB, at most 8 runners per repository. The engine's default
  `keep` now matches `release_host.py` (production 6, others 3). Hosts take it when
  `pakem setup host` runs again.
