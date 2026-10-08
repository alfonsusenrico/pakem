# Deploy design (Phase 2b)

Status: Active (Phase 2b implemented; deviations in section 9)
Last reviewed: 2026-10-08
Sources: Working Standard Profile, section 10; the owner's local CI study of 2026-10-07
(three GitLab and two GitHub Actions pipelines: best-of-breed choices, gaps, template design);
owner decisions for Phase 2b (2026-10-08).

This document is the design for the `deployed` capability. The verify, security, and
database templates in this folder are Phase 2a; the deploy templates (Phase 2b) are
`github/workflows/deploy.yml`, `gitlab/deploy.yml`, and [`../deploy/`](../deploy/README.md).

## 1. Environment model

| Trigger | Target | Gate |
| --- | --- | --- |
| Pull request / merge request | none (verify, security, database only) | required checks |
| Push to `main` | staging, automatically | checks green on the same SHA |
| Tag `v*` whose commit is an ancestor of `main` | production | manual approval (GitHub Environment with required reviewers; GitLab protected environment and `when: manual`) |
| Manual run with a release SHA input | rollback of any environment, production included | same approval as a deploy |

- One deploy per environment at a time: GitHub `concurrency: deploy-<env>` with
  `cancel-in-progress: false`; plus a host-side `flock` (GitLab relies on the host lock
  alone, see section 9).
- Environments carry their public URL so the platform links each deployment.
- Branch protection makes the verify and security checks required before merge.

## 2. Pipeline stages

1. **Build**: container image with the OCI revision label set to the commit SHA, tagged by
   SHA (never only `latest`), pushed once; the same digest is promoted from staging to
   production.
2. **Scan and SBOM**: Trivy image scan pinned by SHA (fails on fixable HIGH/CRITICAL
   findings); an SBOM stored as a pipeline artifact.
3. **Transport**: validate every input before opening a connection; private network access
   through a CI-scoped identity (for example a Tailscale OAuth client limited to `tag:ci`);
   strict `known_hosts` (never `accept-new`); a dedicated deploy key per project, never a
   personal key; secrets only through `env:` or masked, protected variables.
4. **Release on the host** (`release_host.py` pattern):
   - immutable release directory per SHA, verified by image digest; safe extraction;
     ownership validation (`--owner root` mode where the host requires it);
   - in-image preflight (`--require-restricted-environment`) and an isolated candidate
     container check before anything is switched;
   - production only: pre-migration `pg_dump` backup with a recorded location;
   - migrations as a separate step that must succeed before new app instances start;
   - activate, with automatic restore of the previous release when activation or the health
     check fails;
   - health check that returns the deployed revision, first locally on the host, then
     through the public URL;
   - cleanup and label-based image retention only after the public smoke passes;
   - a `deployment.json` record (SHA, digest, time, actor, result).
5. **Smoke**: functional, read-only checks against the public URL.
6. **Notify**: result, environment, SHA, and URL; never secrets or environment values.

## 3. Runtime configuration

- Committed per-environment defaults plus a manifest of required, optional, and paired
  variables with single-line format checks, and declarative production assertions (for
  example secure cookies on, debug off), materialized on the host by a manifest-driven
  `materialize_runtime.py`.
- Host-generated secrets stay on the host; CI never fetches a production environment file
  back to the runner.
- Services bind to loopback or the private network unless they are the public entry point.

## 4. Client-operated hosting

A versioned handoff package built from a tagged commit: artifact, runbook in the client's
language, preconditions, seed and cleanup SQL. Never loose files or archives of uncommitted
work.

## 5. Shared pieces to template

- `release_host.py` (section 2, step 4).
- A generic deploy driver with project hooks (build args, migrate command, health path).
- `materialize_runtime.py` (section 3).
- `smoke.py` for read-only functional checks.
- An idempotent tunnel configuration script that changes only its own route and does not
  restart a shared tunnel on every deploy.
- GitHub: composite actions or reusable workflows pinned by SHA. GitLab: hidden jobs with
  `extends`, `resource_group`, and static cache keys (the org instance rejects file-derived
  cache keys and sets the commit-branch variable inconsistently).
- Dependabot or Renovate configuration for actions, Docker base images, and pip/npm.

## 6. Gaps in current pipelines this phase must close

Pre-migration backup; gated production; per-environment serialization; production rollback
with automatic restore; functional smoke; notifications; release tags with changelog and a
release record; enforced required checks; base images pinned by digest with automated bump
proposals; environment URLs.

Failure patterns seen in the study that the design prevents: insecure defaults in deploy
configuration (for example a cookie `Secure` flag defaulting to off unless a secret is set);
secrets inlined in scripts; `accept-new` host keys; images not tagged by SHA, so a documented
rollback restarts the newest image; web ports bound on all interfaces; a static health
endpoint that cannot prove the revision; any tag deploying production with no gate; health
checks after promotion instead of before; CI images floating away from Dockerfile pins;
deploy keys shared between projects; SHA images never pruned.

## 7. Open decisions for the owner

1. Registry per platform (GHCR or the GitLab registry) and retention counts.
2. Trivy threshold and the format of accepted-risk exceptions.
3. SBOM tool (Syft or BuildKit attestations) and where SBOMs are kept.
4. Backup location, encryption, and retention for pre-migration dumps.
5. Notification channel.
6. Whether `af` copies or references deploy templates into projects.

## 8. Verification gates for Phase 2b

- Each template runs end to end on one pilot project per platform: staging deploy, production
  deploy behind approval, forced failure with automatic restore, manual rollback.
- Tests in `tests/test_ci_templates.py` extended to the deploy templates (pins, permissions,
  `concurrency`/`resource_group`, no secrets in scripts).
- Recorded evidence per gate in the change's `verification.md`.

## 9. Implementation (Phase 2b) and deviations

Implemented as designed: deploy from a commit (`git archive`), immutable release per full
SHA verified by tree digest, safe extraction, host `flock` lock, in-image preflight,
isolated candidate check, pre-migration backup in production, migrations as a separate step
before `up`, activation with automatic restore, revision-matching health check locally then
publicly, cleanup after the public smoke (keep 3 staging, 6 production; images of deleted
releases removed, never running ones), manual rollback for every environment, runtime from
committed defaults plus a manifest (required, optional, pairs, formats, host-generated,
production assertions), host-generated secrets kept on the host, both owner modes (`root`,
`self`), Tailscale plus SSH with a pinned `known_hosts`, no hardcoded hosts, and a
`deployment.json` record.

Deviations from sections 1 to 8, by owner decision or recorded as open work:

1. **Serialization on GitLab.** No `resource_group` (the org instance lacks it); the host
   lock serializes deployments and a second deploy waits `AF_LOCK_WAIT_SECONDS`, then
   fails with exit 75.
2. **Build.** Images are built on the host once per commit (owner decision; a registry is
   later work), tagged `<app>-<environment>/<name>:<sha>`, and verified by the image id
   recorded at build time plus the revision and app labels. The same digest is therefore
   not promoted from staging to production; each environment builds the same commit.
3. **Not implemented yet:** image scan and SBOM (step 2; open decisions 2 and 3),
   notifications (step 6; open decision 5), the tunnel configuration script (the
   `post_promote` hook is the extension point), and Dependabot or Renovate configuration.
4. **Backup.** A project `deploy/hooks/backup` hook writes into a per-deploy
   `AF_BACKUP_DIR`; it is required in production whenever migrations are configured, and
   its location is recorded. Backup retention and encryption stay open (decision 4).
5. **Public check failure.** A failed promote or public smoke also restores the previous
   release automatically, not only a failed activation or local check.
6. **Rollback.** Rollback re-runs the deploy steps for the earlier commit but skips the
   backup and migrations; there is no automatic detection of schema changes across the
   rollback boundary (documented as an owner decision per case). On GitLab a rollback is a
   pipeline run with `AF_ROLLBACK_ENVIRONMENT` and `AF_ROLLBACK_SHA` rather than a manual
   job in every pipeline, so ordinary pipelines are never left blocked; production
   rollback still needs a protected ref and a manual start. Production rollback targets
   must be `v*`-tagged commits on the release branch.
7. **Production gate.** The `v*` tag and ancestor-of-`main` checks run in
   `deploy_remote.sh` for every environment in `AF_PROTECTED_ENVIRONMENTS` (default
   `production`) on both platforms, in addition to the platform gates.
8. **Preflight.** The preflight command is project-defined in `deploy/release.toml`
   (projects pass their own `--require-restricted-environment`); `[]` disables it with a
   warning.
9. **Runtime format.** Runtime files are `NAME='value'` lines, which Compose reads
   literally; values containing a single quote, a line break, or edge whitespace are
   refused. `.env*` files in the archive are skipped, not extracted.
10. **Records.** `deployment.json` plus an append-only `deployments.jsonl` on the host;
    copying the outcome into `PROJECT_STATE.md` stays a manual step. Framework changes
    record evidence in the CHANGELOG and the deploy README instead of a `verification.md`
    (ADR 0005).
11. **Verification gates (section 8).** The templates were verified with unit tests and a
    local Docker sandbox (sshd plus Docker in Docker on loopback: staging in `self` mode,
    production in `root` mode, forced failure with automatic restore, manual rollback,
    retention, the lock). The pilot run on a real GitHub runner and GitLab instance is
    still open.
