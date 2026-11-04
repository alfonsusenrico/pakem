# CI Standard

Status: Active
Applies to: every repository
Last reviewed: 2026-11-04
Review by: 2027-04-07
Sources: comparison of five owner pipelines (2026-10-07); owner Working Standard Profile section 10

## Always
- Triggers: pull/merge requests and pushes to `main`. A release tag names a commit
  already checked on `main`, so tags run no checks of their own.
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

**Where jobs run (no paid minutes unless the project or its profile approves them):** public repositories use GitHub's
hosted runners (`runs-on: ubuntu-latest`), which are free, and never a self-hosted one. A
private repository records its choice as `ci_runner`: GitHub's hosted runners within the
plan's included minutes, or a self-hosted Pakem runner on a host the owner controls
(`runs-on: pakem`; setup in the deployment contracts §11 step 8), which costs no minutes.
Metered minutes beyond the plan need the project's or profile's approval. Keep jobs cheap
either way: one architecture, and cancel superseded runs.

**Documentation-only pull requests** (top-level `*.md`, `docs/`, `openspec/` and nothing
else) skip the heavy jobs inside the workflow: a small `changes` job decides, the heavy jobs
carry `if:` on its output, and a `docs` job runs the documentation checks (`openspec
validate`). Never filter with `paths-ignore`: a required check would then never report and
stay pending. Pushes to `main` always run every check, because a release needs
them on the exact commit it deploys. Anything else, or an unclear change, runs everything.

**Caches on self-hosted runners** stay on the runner's disk: leave the GitHub Actions cache
off (`setup-uv`'s default there; no `enable-cache: true`), since restoring and saving through
GitHub cost minutes per run for files the runner already keeps.
