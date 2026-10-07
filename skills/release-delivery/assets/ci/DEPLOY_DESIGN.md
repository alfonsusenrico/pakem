# Deploy design (Phase 2b)

Status: Draft, Phase 2b. Not approved for implementation; no deploy workflow or release
script exists yet.
Last reviewed: 2026-10-07
Sources: Working Standard Profile, section 10; the owner's local CI study of 2026-10-07
(three GitLab and two GitHub Actions pipelines: best-of-breed choices, gaps, template design).

This document captures the intended design for the `deployed` capability so the Phase 2b
change can start from it. The verify, security, and database templates in this folder are
Phase 2a and already exist.

## 1. Environment model

| Trigger | Target | Gate |
| --- | --- | --- |
| Pull request / merge request | none (verify, security, database only) | required checks |
| Push to `main` | staging, automatically | checks green on the same SHA |
| Tag `v*` whose commit is an ancestor of `main` | production | manual approval (GitHub Environment with required reviewers; GitLab protected environment and `when: manual`) |
| Manual run with a release SHA input | rollback of any environment, production included | same approval as a deploy |

- One deploy per environment at a time: GitHub `concurrency: deploy-<env>` with
  `cancel-in-progress: false`; GitLab `resource_group: <env>`; plus a host-side `flock`.
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
