- `af_bootstrap.sh` prepares a Debian or Ubuntu host for the deployment standard: Docker
  Engine with Compose and Caddy from their signed apt repositories (key fingerprints
  pinned), cosign as a checksum-pinned binary, the `pakem` user, the engine in `/opt/af`,
  `/etc/af`, the deploy root, Caddy's admin socket, and the agent and monitor timers. It
  is idempotent, keeps an existing Docker and a foreign Caddyfile untouched, and ends with
  a `check` that can be rerun any time.
