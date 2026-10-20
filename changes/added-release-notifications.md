- Deployment notifications and a health monitor for the deployment standard:
  `af_notify.py` sends each recorded engine result (with its reason) and each refused
  release to Telegram, Slack, Discord, or a generic webhook configured per host
  (`notify` in the host config); `af_monitor.py`, run by the `af-monitor` systemd timer,
  notifies once when a running release stops reporting its commit and again when it
  recovers. Delivery is best effort and never changes a deployment.
