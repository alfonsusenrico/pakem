# CI Standard

Status: Active
Applies to: every repository
Last reviewed: 2026-10-08
Review by: 2027-04-07
Sources: comparison of five owner pipelines (2026-10-07); owner Working Standard Profile section 10

## Always
- Triggers: pull/merge requests, pushes to `main`, tags.
- Lockfile-checked install (`uv sync --locked`, `npm ci`); runtime versions match the
  Dockerfile.
- Format check, lint, type check, tests, build - the same commands developers run.
- Secret scan (gitleaks), dependency audit (pip-audit, npm audit at high), lint for CI
  files, shell scripts, and Dockerfiles.
- Least-privilege tokens (`permissions: contents: read` by default); secrets only via
  environment, never interpolated into scripts; every third-party action or image
  pinned (actions by commit SHA).
- Timeouts on every job; verify jobs cancel when superseded; required checks enforced
  on `main`.

## `database`
- PostgreSQL service in CI; `REQUIRE_DB=1` so database tests cannot skip.
- Apply migrations to an empty database, apply again (must be a no-op), run drift
  checks, then tests. When the migration runner keeps no checksums, also compare the
  applied revisions with the migration files.

## `deployed`
- Image build labelled with the revision; image scan (HIGH/CRITICAL fail); SBOM kept
  with the release.
- `main` deploys to staging; a `v*` tag that is an ancestor of `main` deploys to
  production behind a manual approval.
- One deployment per environment at a time (concurrency group, or resource group where
  the instance supports it, plus a host lock).
- Post-deploy smoke checks the revision locally and publicly; notifications carry
  environment, revision, and job link only.

The CI templates ship in this skill's `assets/ci/` (GitHub Actions reusable workflows,
GitLab CI hidden jobs, project examples, `README.md` for adoption, and `PINS.md` for
pinned versions). The deploy jobs drive the scripts in `assets/deploy/` (design and
deviations: `assets/ci/DEPLOY_DESIGN.md`).

**Where jobs run (free only, `default:no-billed`):** public repositories use GitHub's
hosted runners (`runs-on: ubuntu-latest`), which are free. Private repositories use a
self-hosted runner on a host we control (`runs-on: [self-hosted, linux, x64, pakem]`;
setup in the deployment contracts §11 step 8), never metered hosted minutes unless the
project approves the cost. Keep jobs cheap either way: one architecture, cancel superseded
runs, and skip heavy jobs for documentation-only changes.
