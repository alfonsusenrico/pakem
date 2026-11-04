- `pakem health` judges free disk space by share alone (critical below 10%, healthy above
  15%), so a small disk is no longer always critical and a large one is judged on the same
  terms. The 20 GiB and 30 GiB floors, sized to one laptop, are 0 (off) unless
  `critical_free_gib` or `healthy_free_gib` is set in `~/.config/pakem/health.toml`. The
  check measures the disk image of Colima or Lima and of OrbStack where it finds one, as
  well as Docker Desktop's, and names whose it is.
