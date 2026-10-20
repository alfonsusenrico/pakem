- Zero-downtime `blue-green` switching in the release engine (opt-in per project): routed
  services start beside the running ones on ephemeral loopback ports, the new containers
  must pass the health check, the host proxy moves to them, and only then do the old ones
  stop; workers are recreated afterwards. A failure before the switch removes only the new
  containers and leaves the old release serving. Requires a host `[proxy]` and
  migrations the previous release can run against (contracts §9).
