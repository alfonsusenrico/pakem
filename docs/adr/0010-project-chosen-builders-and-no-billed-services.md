# 0010. Project-chosen builders and triggers; no billed services

- Status: Proposed
- Date: 2026-10-21

## Context

ADR 0009 made a hosted CI the builder: GitHub Actions builds every `main` commit for two
architectures, signs it with a key kept in CI secrets, publishes to the platform registry,
and runs promotion and rollback as workflow jobs. Three personal projects adopted it.

On 2026-10-21 GitHub stopped starting jobs on the owner's private repositories ("recent
account payments have failed or your spending limit needs to be increased"). Hosted
runner minutes on private repositories are metered; the release workflows (several jobs
per run, an emulated arm64 build) and other private projects shared the free allowance.
The owner's response set two requirements:

- **Nothing billed** unless the specific project approves it: no paid plans, spending
  budgets, or metered usage beyond a free allowance.
- **The builder and the deployment trigger are the project's choice**, never fixed by
  the standard. The owner's practice: work projects build and deploy from the
  self-hosted GitLab to the work project's host; personal projects push to GitHub and the
  owner's own device (synced with GitHub) publishes the release directly; freelance
  projects follow each client's platform.

The owner also wants checks on every push, merge request, and tag before any deployment,
as the work GitLab pipelines run them. Both existing personal pipelines that keep this
(cash-flow-tracker, portfolio) use their own Actions scripts on hosted runners: free on a
public repository, metered on a private one.

What ADR 0009 got right stays: one release engine on every host, signed release records,
build once and deploy by digest, the settings and secrets contract, health checks that
prove the revision, automatic restore, and the pull or push trigger on the host side.

## Decision

1. **No billed services by default.** Agents use only free services and free tiers. A
   project may approve a specific billed service, with its budget, in its `AGENTS.md`
   `## Overrides` (`default:no-billed`). Paid model calls through a gateway are such an
   approval, bounded by the gateway's grants.
2. **Checks and release are separate.** Checks (format, lint, types, tests, build) run on
   every push, merge request, and tag on a free runner the project chooses: hosted runners
   on public repositories, or a self-hosted runner. A release never runs checks of its
   own; it refuses a commit whose required checks did not pass (read from the forge's
   commit status).
3. **The builder is a project declaration.** `deploy/release.toml` gains
   `[build] runner = "local" | "gitlab" | "github"`, and the project's `AGENTS.md` names
   it. Every builder runs the same `af_publish.py` commands (build once for the host
   architecture by default, sign, deliver), so moving a project between builders changes
   configuration, not scripts.
4. **Two delivery modes, chosen per project and host:**
   - **registry** (ADR 0009): the builder publishes images and the signed record to a
     registry; the host's pull agent or a push trigger deploys them. Only with a free
     registry (public packages, or a registry on a host we run).
   - **direct:** the builder sends the signed record, the deploy bundle, and the image
     archive over SSH to the host, through a per-project key restricted by a forced
     command to a receiver. The receiver verifies the signature against the project's
     public key, loads the images, stores the release under `releases/<commit>/`, and
     runs the engine. No registry is involved; rollback uses releases the host kept
     (`keep`).
5. **Images default to the host's architecture** (`linux/amd64` today). A project lists
   more platforms only when a host needs them.
6. **Profiles:**
   - **Personal (GitHub):** checks run on a self-hosted GitHub Actions runner on the
     owner's server (one registration per repository, as personal accounts require),
     under a dedicated user with rootless Docker, so jobs cannot reach the system Docker
     socket, the deploy user, or release secrets. Releases are one local command on the
     owner's device: `release publish` (staging), `release promote vX.Y.Z` (tags and
     promotes), `release rollback <commit>`. Delivery is direct. The signing key stays on
     that device, encrypted, with its password in the system keychain.
   - **Work (GitLab):** the self-hosted GitLab runner runs checks and the same release
     commands; delivery is direct when the registry is disabled.
   - **Freelance:** the client's platform, with the same commands and contracts.

## Consequences

- The kernel gains `default:no-billed`. Projects that already use billed services record
  their approval: the inference gateway (paid provider calls under its global cap) and the
  personal agent (paid replies within its gateway budget).
- Contracts v1 gain additive fields (`[build]`, the direct delivery receiver); existing
  registry projects keep working unchanged. Section 10 of the contracts describes each
  builder instead of GitHub Actions only.
- New pieces: the direct receiver (beside `af_trigger.py`), a local release command
  wrapping `af_publish.py` with the commit-status check, a self-hosted runner setup with
  rootless Docker in `af_bootstrap.sh` (optional, off by default), and CI templates that
  take the runner label from the project.
- Migration for the three personal projects: CI workflows move to the self-hosted runner;
  the publish, promote, and rollback workflows are removed; releases switch to direct
  delivery. Their public registry packages can be deleted once nothing deploys from them.
- A self-hosted runner on the production host still runs repository code there. Rootless
  Docker, a dedicated user, per-job time and memory limits, and repositories only the
  owner can push to keep that acceptable; a host that serves clients does not get one.
- ADR 0009's research note that self-hosted runners are the weakest *deploy transport*
  still holds: here the runner only runs checks, and deployment goes through the
  restricted receiver.
- Open questions for the implementation plan: image archive size and transfer time
  for the larger images, retention of image archives on the host, and whether staging
  shares the production host.
