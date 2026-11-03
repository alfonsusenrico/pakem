- The self-hosted runner's daily Docker clean-up works on hosts with an older BuildKit. It
  failed every day on Debian 13's docker.io 26.1, whose daemon refuses `--max-used-space`
  ("buildkit v0.17.0+ is required"), so the runner's build cache was never bounded. The
  clean-up now falls back to `--reserved-space`, then `--keep-storage`, the same limit under
  the names older buildx versions know. Hosts take it when `pakem setup host` runs again.
- Projects: on the self-hosted runner, drop in-script `docker builder prune
  --max-used-space` calls (they fail the same way) and leave the build cache to the
  runner's daily clean-up.
