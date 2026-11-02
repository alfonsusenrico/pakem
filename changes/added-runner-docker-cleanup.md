- The self-hosted runner's rootless Docker gets a daily clean-up timer: build cache over
  `RUNNER_BUILD_CACHE_MAX` (10 GB) and unused images older than `RUNNER_IMAGE_MAX_AGE` (72 h).
  `af_runner.sh prune` runs it now, and `check` reports whether the timer is active.
