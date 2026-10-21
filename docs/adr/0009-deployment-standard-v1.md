# 0009. Deployment standard v1: build once, one release engine, pluggable triggers

- Status: Proposed
- Date: 2026-10-19

## Context

Five of the owner's pipelines (CI study 2026-10-07) and two newer ones each deploy
differently: CI pushing over SSH and building on the host, a restricted SSH receiver, a
self-hosted runner pulling a registry image, and a host-built release with a `current`
symlink. The Phase 2b deploy templates (`release-delivery` `assets/deploy/`) were never
adopted, and a new project (an inference gateway) built its own pipeline instead. Gaps
recur across them: no automatic restore after a failed start, no backup before
migrations, health checks that cannot prove the running revision, no lock on some hosts,
rollback only in some environments, secrets copied from CI to hosts, and nothing
standard for ingress, scheduled backups, or alerting.

The owner wants one baseline for personal projects, freelance projects on hosts we run,
and work projects: deployable to any Linux host by configuration alone. It must be
settled enough that later changes are additive. Research (2026-10-19) on current practice:

- Build once and promote the same artifact by digest; rebuilding per environment can
  yield different software. Sign by digest and verify before deploy.
- GitOps practice is pull: an agent on the target applies the desired state, so CI holds
  no credentials to the target. Pull tooling is mature on Kubernetes (Argo CD, Flux) but
  immature for plain Docker hosts (single-maintainer pre-1.0 tools; Watchtower archived).
- Self-hosted CI runners on a deploy target are persistent and run repository code; they
  are the weakest transport.
- Tag-triggered production is established when the tag promotes an already tested
  artifact rather than triggering a build. GitHub required reviewers on environments are
  unavailable for private repositories on free plans; a protected tag is then the gate.
- Configuration lives outside the artifact (twelve-factor); secrets are the exception that
  belongs in mounted files, not environment variables.

## Decision

The standard has three swappable layers and five fixed contracts. Projects depend only on
the contracts.

### Layers

1. **CI (build and publish)**, GitHub Actions in v1 (see Consequences): verify, build the
   image once per `main` commit for `linux/amd64` and `linux/arm64`, scan, SBOM, push to the
   platform registry with a deploy bundle (Compose and project deploy files of the same
   commit), sign every digest with the project's release signing key (cosign key pair;
   private repositories skip the public transparency log), and publish a signed release
   record. CI never builds again for staging or production. Keyless signing is not the
   default: it writes private repository identities to a public log and does not work with
   a self-managed GitLab without running a Sigstore stack.
2. **Release engine**, one program on every host, the only thing that changes a running
   environment. Fixed sequence: lock; verify record and image signatures; pull by digest;
   preflight inside the image; backup hook (required in production when the project has
   migrations); migrations as a one-off container; switch; health check that must report
   the expected revision; keep, or restore the previous release; promote; record; notify;
   retention. Evolves from `release_host.py`.
3. **Trigger**, how the engine learns about a new release, chosen per host by
   configuration:
   - **pull (default on hosts we control):** an agent polls the environment's release
     pointer and runs the engine; CI holds no host credentials; the host needs no inbound
     access;
   - **push (hosts where an agent is not allowed):** CI connects over SSH with a
     per-project key restricted by a forced command to the engine, through a private
     network or a pinned public endpoint.

### Contracts (changing one is a MAJOR version of the standard)

1. **Release record**: JSON with project, version, commit, image digest per service,
   schema version, build time, and CI run link; signed. Mutable names never identify what
   runs.
2. **Release pointer**: per environment, an OCI artifact in the project's registry
   (`<image>:release-<environment>`) holding the signed record. Any OCI registry, GHCR
   included, stores it; CI needs only the registry access it already has.
3. **Engine interface**: steps, project hooks (`pre_migrate`, `backup`, `post_switch`),
   exit codes, and the deployment record format.
4. **Settings manifest**: every runtime name with kind (`default`, `placement`, `secret`,
   `host_generated`), required or optional, single-line format, and production
   assertions. Defaults are committed per environment; placement values (host, domain,
   ports, data paths, networks) are host or CI variables; secrets are created on or
   delivered once to the host, stored as private files, mounted read-only and read through
   the `_FILE` convention, and never travel in images, logs, or (in pull mode) CI.
5. **Migration rule**: every release must run against the schema of the release before it
   (expand, then contract). The engine never restores automatically across a schema
   change; it stops, and recovery is a reviewed restore from the pre-migration backup.

### Environments and promotion

| Environment | Trigger | Artifact | Gate |
| --- | --- | --- | --- |
| local | the developer, from the working tree | built locally | none; same settings manifest, `setup-local` generates secrets |
| staging | merge to `main` | the image built for that commit | required CI checks |
| production | protected tag `vX.Y.Z` on a `main` commit that staging received | the same digests, promoted, never rebuilt | the tag is the owner's acceptance of staging; protected tags, environments, or reviewers where the plan allows |

Rollback is a manual promotion of an earlier release record through the same engine.
Staging may share a host with production when the base directory, Compose project, ports,
volumes, and database are separate.

### Host services

- **Ingress:** one reverse proxy per host (Caddy) terminates TLS and routes domains to
  loopback-bound services; a tunnel may sit in front when the host has no public address.
  The engine changes only its own project's routes.
- **Zero-downtime:** designed in, opt-in per project. The default switch is stop, then
  start; opted-in projects start the new release beside the old one and the engine moves
  the proxy route after the health check.
- **Backups:** projects with data declare a backup hook, and the engine runs it before
  migrations (required in production). Scheduled backups, encryption, off-host copies, and
  restore drills are each project's own responsibility, not a host service (owner
  decision, 2026-10-20).
- **Alerting:** the engine notifies every deployment result through a configured channel;
  a per-host check reports failing health; container logs are size-bounded.

### Distribution and versioning

Projects copy the CI templates and project files with a version stamp; `pakem project check`
reports drift. Hosts install the engine and agent from a tagged framework release. The
standard is versioned on its own: v1.0 after the pilots below; additive changes are
MINOR; changing a contract needs a new ADR and a MAJOR version.

### Out of scope

Managed platforms (serverless, PaaS), Windows hosts, and multi-host or autoscaled
environments. A project that outgrows one host per environment moves to Kubernetes with
Flux or Argo CD, keeping CI, records, and promotion unchanged. Client-operated hosting
keeps the handoff package.

## Consequences

- One mechanism, three layers; each project adds only hooks and its settings manifest.
- We own two programs (engine, pull agent). Mitigation: the engine grows from existing
  tested code, the agent stays small, and both ship only after the pilots and drills.
- Work projects adopt only with their team's agreement; until then they can take the
  engine with the push trigger, which closes their restore, backup, and lock gaps.
- Platform limits (2026-10-19): on GitHub Free, private repositories get no required
  reviewers or wait timers, and artifact attestations need Enterprise Cloud; signing with
  a project key works on every plan and platform. The engine cannot see staging from a
  production host, so "staging succeeded" is the owner's judgement expressed by the tag,
  while CI checks that staging received the commit.
- v1 targets GitHub, where the owner's personal and freelance projects live. The contracts
  and `af_publish.py` are platform-neutral, so a GitLab CI template is a later addition
  for the first project that needs it. The organization's GitLab (checked 2026-10-20)
  has its container registry disabled and predates 12.5, so it would need a separate OCI
  registry or an enabled and tested built-in one first.
- Supersedes the Phase 2b deploy design where they differ (host-built images, SSH-only
  transport, backup retention left open).
- Pilots before v1.0: one GitHub project; drills for a failed start, a
  failed migration, rollback, a host move, and a restore.
