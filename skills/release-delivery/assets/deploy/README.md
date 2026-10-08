# Deploy templates

Deployment for the `deployed` capability on hosts we or our pipeline control, implementing
[the deployment standard](../../references/deployment.md) and the
[deploy design](../ci/DEPLOY_DESIGN.md). CI connects over a private network (Tailscale) and
SSH, and the host builds, checks, and starts one immutable release per commit with Docker
Compose. The CI wiring is `github/workflows/deploy.yml` and `gitlab/deploy.yml` in
[`assets/ci`](../ci/README.md).

| File | Runs | Purpose |
| --- | --- | --- |
| `deploy_remote.sh` | CI (POSIX sh) | Validates inputs, archives the commit, materializes the runtime, uploads, runs the host sequence under the lock, public smoke, record |
| `release_host.py` | host (Python 3.11+) | `prepare`, `secure-file`, `lock`, `activate`, `promote`, `rollback`, `cleanup`, `record`, `status` |
| `materialize_runtime.py` | CI (Python 3.11+) | Builds the runtime file from `deploy/runtime.manifest.toml` and CI variables; never prints values |
| `smoke.py` | CI or host | Revision check through the health path, then read-only endpoints |
| `release.example.toml` | - | Template for the project's `deploy/release.toml` |
| `runtime.manifest.example.toml` | - | Template for the project's `deploy/runtime.manifest.toml` |

## Adopting

1. Copy the four scripts into the project at `deploy/af/` (record the framework version in
   a comment right after each shebang line), and copy the CI template:
   GitHub `deploy.yml` into `.github/workflows/` plus a caller like
   `github/deploy.example.yml`; GitLab `deploy.yml` included like `gitlab/ci.example.yml`.
2. Write `deploy/release.toml` and `deploy/runtime.manifest.toml` from the examples, the
   committed defaults files they name (`deploy/runtime/<env>.env`, no secrets), and
   optional hooks in `deploy/hooks/`.
3. Make the image report its revision: the host builds with
   `--build-arg AF_REVISION=<sha>` and the label `org.opencontainers.image.revision=<sha>`;
   bake `AF_REVISION` into the health response (`health_path`, JSON key `revision_key`).
   A health response computed from an environment variable cannot prove which image runs.
4. In `compose.yaml`, use `image: ${AF_IMAGE_<NAME>}` (no `build:`), `env_file:
   ${AF_RUNTIME_FILE}`, and bind published ports to `127.0.0.1` (ingress through the tunnel
   or reverse proxy). Use named volumes for data; they persist across releases because the
   Compose project name is stable (`<app>-<environment>`).
5. Prepare each host (below), add the CI variables, and run one staging deploy, one forced
   failure (automatic restore), and one rollback before relying on it.

## Host sequence

`deploy_remote.sh` uploads to a private `/tmp/af-upload.*` directory, takes the lock, and
calls `release_host.py` for each step; any failure stops the sequence.

1. `lock --hold`: one deployment per environment (`flock` on `<base>/deploy.lock`); waits
   `AF_LOCK_WAIT_SECONDS`, then exits 75. The lock is released when the CI job ends, even
   if it is killed (the SSH session closes).
2. `secure-file`: moves the uploads into `<base>/incoming/` (0600, owner-only).
3. `prepare`: safe extraction (no absolute paths, `..`, links leaving the release, devices,
   or hard links; `.env*` files are skipped) into `releases/<sha>/source/`, made read-only
   and recorded by tree digest. A redeploy of the same commit must match the digest; a
   modified release is refused. Installs `runtime.env.next` (0600) with host-generated
   secrets merged in.
4. `activate`: `pre_build` hook; image build once per commit (verified later by image id
   and labels); in-image `preflight`; isolated candidate container on an ephemeral
   loopback port with its revision checked; `backup` hook (required in production when
   `migrate` is set); `pre_migrate` hook; `migrate` in a one-off container; `docker compose
   up --wait`; local revision check. A failure from `up` onwards restores the previous
   release automatically (exit 3, or 4 when the restore fails too).
5. `promote`: refuses unless every running container of the project carries the new
   revision label, then switches `current` atomically and runs `post_promote`.
6. Public smoke from CI (`smoke.py`): the public URL must serve the new revision, then
   each `AF_SMOKE_PATHS` entry must answer 2xx. A failure restores the previous release.
7. `cleanup`: keeps the `keep` most recently promoted releases (3 staging, 6 production by
   default) plus `current` and anything running; removes the others and their image tags.
   Images of running releases are never removed.
8. `record`: `<base>/deployment.json` and an appended `<base>/deployments.jsonl` line
   (sha, mode, result, time, actor, pipeline URL, archive and tree digests, image ids,
   backup location). Copy the outcome into `PROJECT_STATE.md`.

Host layout: `<DEPLOY_ROOT>/<environment>/` holds `releases/<sha>/{source/,release.json,
runtime.env}`, `current -> releases/<sha>`, `shared/host-generated.env`, `backups/`,
`incoming/`, `deploy.lock`, `environment`, and the records.

## Owner modes

| | `DEPLOY_OWNER=root` (organization hosts) | `DEPLOY_OWNER=self` (personal server) |
| --- | --- | --- |
| Runs as | root, through `sudo -n` when `DEPLOY_USER` is not root | the non-root deploy user |
| Tool | an installed, root-owned copy at `DEPLOY_HOST_TOOL`; CI checks it matches its own copy | the uploaded copy (or `DEPLOY_HOST_TOOL` when set) |
| Ownership | everything `root:root`, directories 0750 | the deploy user, directories 0700 |
| Refuses | running as non-root | running as root |

Both modes refuse a base, or any ancestor, that is a symlink, owned by another user, or
group- or world-writable (root-owned sticky directories such as `/tmp` excepted).

Host preparation (once, by an administrator):

- Docker Engine with the Compose plugin and Python 3.11+ at `DEPLOY_REMOTE_PYTHON`
  (default `/usr/bin/python3`).
- A dedicated deploy user with an `authorized_keys` entry for this project's deploy key
  only (never a personal key). In `self` mode the user is in the `docker` group, which is
  root-equivalent on that host; prefer `root` mode on shared hosts.
- `DEPLOY_ROOT` created and owned by root (root mode) or the deploy user (self mode).
- Root mode: install `release_host.py` root-owned (for example
  `/usr/local/libexec/af-deploy/release_host.py`, 0644) and allow exactly that command:
  `deploy ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/libexec/af-deploy/release_host.py *`.
  Reinstall it when the framework copy changes (the deploy fails until then).
- Pin the host key: take the key from the host itself (or `ssh-keyscan` over the tailnet,
  checking the fingerprint against the host's console) and store the `known_hosts` line
  as `DEPLOY_KNOWN_HOSTS`. Host keys are never accepted on first use.

## Variables

Set per environment (GitHub environment variables and secrets; GitLab CI/CD variables
scoped to the environment, protected and masked where secret). Values are never printed.

| Name | Kind | Meaning |
| --- | --- | --- |
| `DEPLOY_HOST` | variable | Host name or IPv4 address on the private network |
| `DEPLOY_USER` | variable | SSH user (the deploy user) |
| `DEPLOY_SSH_PORT` | variable | SSH port (default 22) |
| `DEPLOY_ROOT` | variable | Project directory on the host; the base is `<DEPLOY_ROOT>/<environment>` |
| `DEPLOY_OWNER` | variable | `root` or `self` |
| `DEPLOY_HOST_TOOL` | variable | Installed `release_host.py` (required for root mode with a non-root user) |
| `DEPLOY_REMOTE_PYTHON` | variable | Python on the host (default `/usr/bin/python3`) |
| `DEPLOY_PUBLIC_URL` | variable | Public base URL (https; http only on loopback) |
| `DEPLOY_KNOWN_HOSTS` | variable (GitLab: File) | Pinned `known_hosts` line(s) for the host |
| `DEPLOY_SSH_PRIVATE_KEY` | secret (GitLab: File, protected) | The project's dedicated deploy key |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | secrets (GitHub) | Tailscale OAuth client limited to `tag:ci` (`auth_keys` scope) |
| runtime names | secrets or variables | Exactly the names in `deploy/runtime.manifest.toml` |

`deploy_remote.sh` also reads `AF_DEPLOY_ENVIRONMENT`, `AF_DEPLOY_SHA`, `AF_DEPLOY_MODE`
(`deploy` or `rollback`), `DEPLOY_SSH_KEY_FILE` and `DEPLOY_KNOWN_HOSTS_FILE` (paths, set by
the CI templates), `AF_PROTECTED_ENVIRONMENTS` (default `production`: only `v*`-tagged
commits that are ancestors of `AF_RELEASE_REF`), `AF_RELEASE_REF`, `AF_SMOKE_PATHS`,
`AF_SMOKE_ATTEMPTS`, `AF_LOCK_WAIT_SECONDS`, `AF_DEPLOY_ACTOR`, `AF_PIPELINE_URL`,
`AF_REPO_DIR`, and `AF_PYTHON`. On GitHub the runtime values arrive as `toJSON(vars)` and
`toJSON(secrets)` in `AF_RUNTIME_VARS_JSON` and `AF_RUNTIME_SECRETS_JSON`; only manifest
names are read.

## Runtime configuration

`materialize_runtime.py` merges the committed defaults for the environment with the
`required` and `optional` values from CI, checks `pairs`, single-line `formats`, and the
production `prod_assert` values, and refuses a required or host-generated name in the
committed defaults, a host-generated name supplied by CI, and any value that is multi-line,
padded with whitespace, or contains a single quote. It writes `NAME='value'` lines (0600),
which Docker Compose reads literally (no `$` interpolation). `host_generated` names are
created once on the host (`shared/host-generated.env`, 0600) and never leave it.

## Hooks

Executable files in the commit's `deploy/hooks/`, run on the host from the release source
with `AF_ENVIRONMENT`, `AF_RELEASE_SHA`, `AF_BASE`, `AF_RELEASE_DIR`, `AF_SOURCE_DIR`,
`AF_RUNTIME_FILE`, `AF_COMPOSE_PROJECT`, and `AF_IMAGE_<NAME>` set. A non-zero exit stops
the deploy. In root mode hooks run as root.

| Hook | When | Typical use |
| --- | --- | --- |
| `pre_build` | before the image build | build arguments, generated assets |
| `backup` | before migrations; required in production when `migrate` is set | `pg_dump` into `AF_BACKUP_DIR` (must write at least one file) |
| `pre_migrate` | after the backup, before migrations | checks that the database is reachable |
| `post_promote` | after `current` switches | reload ingress or tunnel configuration (change only this project's route) |

Backups are kept; their retention, encryption, and off-host copy are the project's
decision (design open decision 4).

## Rollback

- **CI (every environment):** GitHub `workflow_dispatch` with `mode: rollback` and the
  release's full SHA; GitLab: run a pipeline with `AF_ROLLBACK_ENVIRONMENT` and
  `AF_ROLLBACK_SHA`. Production rollback needs the same approval as a deploy and a `v*`
  tagged commit. The commit is re-archived and verified against the retained release (or
  prepared again), the runtime is materialized from current variables, and the release is
  activated and promoted with the same checks, restore, smoke, cleanup, and record.
- **On the host** (when CI is unavailable):
  `release_host.py lock --base <base> --owner <mode> -- python3 release_host.py rollback
  --base <base> --owner <mode> --environment <env>` returns to the previously promoted
  release with its own runtime.
- Rollback never runs the backup, `pre_migrate`, or migrations. Releases must stay
  compatible with the schema one release ahead (expand, then contract); a rollback across
  an incompatible schema change needs the owner's explicit decision and a reviewed
  down-migration or a restore from the pre-migration backup.

## Exit codes

`0` success; `1` failed with nothing to restore; `2` invalid input or state (nothing
changed); `3` failed and the previous release was restored; `4` failed and the restore
failed too (check `release_host.py status`, act manually); `75` another deployment holds the
lock.

## Verification of these templates

- `python3 -m unittest discover -s tests` runs the unit tests (`tests/test_deploy_*.py`:
  extraction, digests, owner checks, lock, activation order, restore, rollback, retention,
  records, runtime validation, smoke, and input validation before SSH).
- `AF_SANDBOX=1 python3 -m unittest tests.test_deploy_sandbox -v` deploys a sample app
  through `deploy_remote.sh` into a throwaway local Docker sandbox (docker:dind with sshd,
  bound to 127.0.0.1, removed afterwards): two commits, a failing release restored
  automatically, a manual rollback, retention, the lock, and production in root mode.
- Not yet exercised on a real GitHub runner, GitLab instance, or Tailscale network.
