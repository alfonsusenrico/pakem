- `af eval run --host antigravity` passes `--dangerously-skip-permissions` (with
  `--sandbox`): headless agy ended every run at the first command outside the global
  allow list, so command-dependent scenarios measured the harness, not compliance.
