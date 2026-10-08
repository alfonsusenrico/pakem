# Deployment

Status: Active
Applies to: projects with the `deployed` capability on hosts we or our pipeline control
Last reviewed: 2026-10-08
Review by: 2027-04-07
Sources: owner release pipelines (2026); comparison of five owner pipelines (2026-10-07)

1. **Source**: a `git archive` of the exact commit; never loose files.
2. **Transport**: VPN (for example Tailscale) and SSH with a dedicated deploy key and a
   pinned host key; inputs validated before connecting; a non-root deploy user where
   possible.
3. **Runtime configuration**: committed per-environment defaults plus required and
   optional variables from the CI secret store, validated (single-line values,
   production assertions such as secure cookies and debug off). Host-generated secrets
   stay on the host and never travel back to CI.
4. **Release directory**: `releases/<full-sha>/`, immutable (a redeploy of the same
   commit must match byte for byte), owned and permissioned strictly, extracted
   safely (no `..`, no absolute paths, no `.env*`).
5. **Lock**: one deployment per environment (`flock` on the host plus pipeline
   concurrency).
6. **Build and preflight**: build the image tagged with the commit; run the in-image
   preflight (configuration validation without printing values); start an isolated
   candidate and check its revision.
7. **Backup and migrate**: production takes a database backup, then runs migrations as
   a separate step before new instances start.
8. **Activate**: start the new release, check health locally (the health endpoint
   reports the running revision), promote atomically (`current` symlink); on any
   failure restore the previous release automatically.
9. **Public check**: verify the public URL serves the new revision; then clean up old
   releases (keep 3 on staging, 6 on production), removing their images, never the
   running ones.
10. **Rollback**: a manual job for every environment that redeploys a previous release
    commit through the same steps; schema changes across the rollback boundary need
    explicit confirmation.
11. **Record**: deployed revision, image id, time, and result in the release record and
    `PROJECT_STATE.md`.

Ingress through a tunnel (for example Cloudflare Tunnel) binds services to loopback
only; tunnel configuration changes are validated, backed up, applied only when
changed, and checked publicly.

Templates: this skill's `assets/deploy/` (`deploy_remote.sh`, `release_host.py`,
`materialize_runtime.py`, `smoke.py`, adoption and host setup in its `README.md`) with
the CI jobs in `assets/ci/`.
