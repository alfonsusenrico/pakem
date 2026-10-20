- Release triggers for the deployment standard: `af_trigger.py poll`, a pull agent run by
  the `af-agent` systemd timer, and `af_trigger.py receive`, a forced-command SSH receiver
  that accepts only `deploy <environment> [<actor>]` or `status <environment>`. Both run
  the same engine. GitHub template `workflows/push-trigger.yml`, optional push jobs in
  `release.example.yml`, and host setup steps in the deployment contracts (§11).
