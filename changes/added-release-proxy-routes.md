- Proxy routes for the deployment standard: on hosts with a `[proxy]` table the engine
  writes each project environment's Caddy site file from its `[[routes]]` after the health
  check, reloads Caddy through its admin socket, and puts the old file back when Caddy
  refuses it or the old release is restored. Domains and ports are validated, so placement
  values cannot inject proxy configuration. Host Caddyfile template in
  `assets/deploy/caddy/`; host setup step in the deployment contracts (§11).
