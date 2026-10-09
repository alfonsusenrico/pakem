# CI templates

Verify, security, database, and deploy pipelines for GitHub Actions (reusable workflows) and
GitLab CI (hidden jobs used through `include` and `extends`), implementing the Working
Standard Profile, section 10. The deploy jobs drive the scripts in
[`../deploy/`](../deploy/README.md) (adoption, host setup, variables, hooks, rollback); the
design and its deviations are in [DEPLOY_DESIGN.md](DEPLOY_DESIGN.md). Image scan, SBOM,
and notifications are not here yet.

Pinned versions, SHAs, and checksums: [PINS.md](PINS.md).

## Which templates a project uses

| Capability (from `AGENTS.md`) | GitHub reusable workflow | GitLab hidden jobs |
| --- | --- | --- |
| always, Python service | `verify-python.yml` | `.af:verify-python` |
| always, Node/TypeScript app | `verify-node.yml` | `.af:verify-node` |
| always (required before `deployed`) | `security.yml` | `.af:secret-scan`, `.af:python-audit`, `.af:node-audit`, `.af:shellcheck`, `.af:hadolint` |
| `database` | `database.yml` | `.af:database` |
| `deployed` | `deploy.yml` (with `github/deploy.example.yml`) | `.af:deploy`, `.af:rollback`, `.af:smoke` |

A repository with both a backend and a frontend calls both verify templates.
`github/ci.example.yml`, `github/deploy.example.yml`, and `gitlab/ci.example.yml` show
complete project callers.

## What a project must provide

- Python: `uv.lock`, and `ruff`, `pyright`, `pytest` as dev dependencies
  (`the project-bootstrap skill assets/tooling/python`).
- Node: `package-lock.json` and the npm scripts `format:check`, `lint`, `typecheck`, `test`,
  `build` (`the project-bootstrap skill assets/tooling/node`).
- `database`: an idempotent migrate command; tests that read `TEST_DATABASE_URL` and fail,
  never skip, when `REQUIRE_DB=1` (contract example in `the project-bootstrap skill assets/tooling/python/README.md`).
  Migrations may also read `DATABASE_URL`, which points at the same disposable database.
- Optional `.gitleaks.toml` and `.gitleaksignore` at the repository root; gitleaks reads them
  automatically.

## Parameters

### GitHub inputs

| Workflow | Inputs (default) |
| --- | --- |
| verify-python | `python_dir` (`.`), `python_version` (empty: `.python-version` or `requires-python`), `uv_version` (`0.12.23`), `typecheck_cmd` (`uv run pyright`), `test_cmd` (`uv run pytest`), `test_args`, `runner` (`ubuntu-24.04`), `timeout_minutes` (20) |
| verify-node | `node_dir` (`.`), `node_version` (`24`), `build_env` (non-secret `NAME=value` lines), `test_cmd` (`npm test`), `runner`, `timeout_minutes` (20) |
| security | `python_dir` and `node_dir` (empty skips that audit), `uv_version`, `node_version`, `runner`, `timeout_minutes` (10 per job) |
| database | `stack` (`python` or `node`), `app_dir`, `python_version`, `uv_version`, `node_version`, `install_cmd` (empty: `uv sync --locked` or `npm ci`), `migrate_cmd` (required), `migrate_check_cmd`, `test_cmd` (required), `postgres_image` (`postgres:17`), `runner`, `timeout_minutes` (20) |
| deploy | `environment` and `sha` (required), `mode` (`deploy` or `rollback`), `tools_dir` (`deploy/af`), `release_ref` (`origin/main`), `protected_environments` (`production`), `smoke_paths`, `tailscale` (true), `tailscale_tags` (`tag:ci`), `runner`, `timeout_minutes` (30); environment variables and secrets per [the deploy README](../deploy/README.md#variables); callers pass `secrets: inherit` |

### GitLab variables

| Hidden job | Variables (default) |
| --- | --- |
| `.af:verify-python` | `PYTHON_DIR` (`.`), `TYPECHECK_CMD`, `TEST_CMD`, `TEST_ARGS`, `AF_PYTHON_IMAGE` |
| `.af:verify-node` | `NODE_DIR` (`.`), `TEST_CMD` (`npm test`), `AF_NODE_IMAGE`; build-time values are job variables |
| `.af:secret-scan` | `AF_GITLEAKS_IMAGE` |
| `.af:python-audit` / `.af:node-audit` | `PYTHON_DIR` / `NODE_DIR`, `PIP_AUDIT_VERSION`, image variables |
| `.af:shellcheck` / `.af:hadolint` | image variables; jobs run only when matching files exist |
| `.af:database` | `APP_DIR`, `INSTALL_CMD` (`uv sync --locked`), `MIGRATE_CMD` and `TEST_CMD` (required), `MIGRATE_CHECK_CMD`, `AF_DATABASE_IMAGE`, `AF_POSTGRES_IMAGE` (`postgres:17.9-trixie`) |
| `.af:deploy` / `.af:rollback` | `AF_DEPLOY_ENVIRONMENT` (required), `AF_TOOLS_DIR` (`deploy/af`), `AF_PROTECTED_ENVIRONMENTS` (`production`), `AF_SMOKE_PATHS`, `AF_LOCK_WAIT_SECONDS` (600), `AF_DEPLOY_IMAGE`; rollback pipelines set `AF_ROLLBACK_ENVIRONMENT` and `AF_ROLLBACK_SHA`; host variables per [the deploy README](../deploy/README.md#variables) |
| `.af:smoke` | `AF_DEPLOY_ENVIRONMENT` (required), `AF_SMOKE_PATHS`, `AF_TOOLS_DIR`, `AF_DEPLOY_IMAGE` |

Runner tags are set once in the project file (`default: tags:`), not in the templates.

## Copy or reference

**GitHub.** A reusable workflow can be referenced from another repository only when it is
stored directly in that repository's `.github/workflows/` directory (subdirectories are not
supported), as `{owner}/{repo}/.github/workflows/{file}@{ref}`. The framework keeps its
templates in `assets/ci/github/workflows/`, so a project either:

1. **Copies** the workflows it needs into its own `.github/workflows/` and calls them with
   `uses: ./.github/workflows/<name>.yml`. Add a first line recording the source, for example
   `# Copied from agent-framework <VERSION> assets/ci/github/workflows/security.yml`, and
   re-copy when the framework releases a change. This is the default for personal repositories.
2. **References** a shared CI repository that holds the same files in its
   `.github/workflows/`, pinned by full commit SHA:
   `uses: <owner>/<ci-repo>/.github/workflows/security.yml@<40-char-sha> # vX.Y.Z`.
   A private CI repository owned by a personal account must enable *Settings > Actions >
   General > Access > "Accessible from repositories owned by the user"*, and then only that
   user's private repositories can call it; public repositories cannot.

**GitLab.** `include:project` can read any path, so a profile repository (for example
`<group>/agent-profile-ci`) can include these files directly, pinned with `ref:` to a release
tag or commit SHA. Alternatively copy them into the project and use `include: local`.

`af` does not automate copying yet.

## Rules the templates follow

- Every third-party action is pinned to a full 40-character commit SHA with a `# vX.Y.Z`
  comment; downloaded tools are verified against SHA-256 checksums; images use exact version
  tags (digests listed in [PINS.md](PINS.md)).
- Top-level `permissions: contents: read`, repeated per job; no job needs more.
- Every job has `timeout-minutes` (GitHub) or `timeout` plus `interruptible: true` (GitLab),
  except deploy and rollback jobs, which are `interruptible: false` (a deploy is never
  cancelled halfway). Deploys are serialized per environment by GitHub `concurrency`
  (`cancel-in-progress: false`) and, on both platforms, by the host lock; GitLab templates
  do not use `resource_group`, which the org instance lacks.
- SSH host keys are pinned (`StrictHostKeyChecking=yes` with a stored `known_hosts`), never
  accepted on first use, and no template names a real host.
- `run:` blocks never contain `${{ }}` expressions: values, and any secret a project adds,
  reach scripts through `env:` (GitHub) or masked, protected CI/CD variables (GitLab).
  `actions/checkout` runs with `persist-credentials: false`.
- Commands passed as inputs (`test_cmd`, `migrate_cmd`, ...) run with `eval`; they come from
  the project's own CI file, never from event data such as pull request titles.
- GitLab jobs use `CI_COMMIT_REF_NAME` and static cache keys (`af-uv-v1`, `af-npm-v1`): the
  org instance rejects file-derived cache keys and sets the commit-branch variable
  inconsistently.
- The GitHub security tools are linux x86_64 binaries; use an x86_64 runner. Self-hosted
  runners need Actions Runner v2.327.1 or later for the node24-based actions.

## Verification of these templates

- `python3 -m unittest discover -s tests` includes `tests/test_ci_templates.py` (pins, pins
  recorded in PINS.md, permissions, timeouts, no expressions or secrets in `run:`,
  `REQUIRE_DB`, GitLab constructs, image tags, deploy inputs, per-environment concurrency,
  secrets only through `env:`/`with:`, the production gate, no `resource_group`, pinned
  host keys) and the deploy script tests (`tests/test_deploy_*.py`).
- actionlint 1.7.12 with shellcheck 0.11.0 and check-jsonschema's built-in GitHub workflow
  and GitLab CI schemas passed on 2026-10-07; on 2026-10-08 actionlint 1.7.12 (container
  image) and check-jsonschema 0.38.2 passed on the deploy templates and the updated GitLab
  example, and shellcheck 0.11.0 on `deploy_remote.sh`.
- The deploy scripts ran end to end in the local Docker sandbox (`AF_SANDBOX=1`, see the
  deploy README).
- Not yet exercised on a real GitHub runner or GitLab instance; run each template once on a
  pilot project before relying on it.
