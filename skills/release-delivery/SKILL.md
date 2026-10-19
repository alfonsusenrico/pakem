---
name: release-delivery
description: Use when setting up or changing CI pipelines, preparing a release, tag, or deployment, writing rollback or runbook notes, verifying a deployed revision, packaging a deployment for a client, preparing a client handover, or reviewing documentation after a release.
---

# Release and Delivery

Production actions need the owner's explicit instruction (`sensitive-operations`).

## 1. CI

Every repository has CI from the first commit. Use the CI templates
([CI standard](references/ci.md); [adoption guide](assets/ci/README.md)): always format, lint, type check, tests, build,
secret scan, dependency audit; plus database and deployment jobs switched on by
capabilities.

## 2. Deployment

For servers you or your pipeline control, follow [deployment](references/deployment.md):
deploy from a commit only, immutable release per commit, preflight, migrate, activate
with automatic restore, revision-matching health check, rollback for every
environment, one deploy per environment at a time. Use the
[deploy templates](assets/deploy/README.md).

The standard is moving to [deployment contracts v1](references/deployment-contracts.md)
(ADR 0009, draft until standard v1.0): CI builds once and signs, one release engine per
host, pull or push trigger. Design new pipelines against the contracts; the current
templates still implement the earlier host-build design.

For client-operated hosting, build a versioned handoff package from a tagged commit
([handover](references/handover.md)). Never deploy loose files or zips of
uncommitted work.

## 3. Releases

- Production releases are tagged `vMAJOR.MINOR.PATCH` on `main`; staging deploys from
  `main`.
- Each release has notes (CHANGELOG entry or release description): changes, migrations,
  configuration changes, rollback notes, known limits.
- Evidence (deployed revision, health, smoke results) is tied to the exact commit and
  recorded in `PROJECT_STATE.md` and the change's `verification.md`.

## 4. Operational alerting

Follow [operational alerting](references/operational-alerting.md) for failure reports
and notifications.

## 5. After release

Review documentation against the deployed behaviour: runbooks, configuration names,
commands, ownership, known constraints. Update only what changed.

## 6. Client handover

Every handover includes the items in [handover](references/handover.md), using the
[runbook template](assets/handover/runbook.md.tmpl) and the
[user guide template](assets/handover/user-guide.md.tmpl), in the client's language.
