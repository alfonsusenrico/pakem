# Deployment contracts v1

Status: Draft (ADR 0009; becomes Active with deployment standard v1.0)
Applies to: projects with the `deployed` capability on hosts we or our pipeline control
Last reviewed: 2026-10-20
Review by: 2027-04-19
Sources: ADR 0009; owner pipeline study 2026-10-07; deployment research 2026-10-19

Projects depend only on these contracts. CI templates, the release engine, and the
triggers may change behind them. Changing a contract is a MAJOR version of the standard
and needs an ADR. Machine checks: `assets/deploy/contracts.py`; examples in
`assets/deploy/contracts/`.

## 1. Release record

What a release is: one JSON document (media type `application/vnd.af.release.v1+json`),
created by CI for a `main` commit, signed, and never edited.

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | string | `af.release/v1` |
| `project` | string | `[a-z][a-z0-9-]{0,62}`, stable for the project's life |
| `commit` | string | full 40-character lowercase commit SHA |
| `version` | string or null | `vMAJOR.MINOR.PATCH` for a production promotion, else null |
| `created` | string | UTC time, `YYYY-MM-DDTHH:MM:SSZ` |
| `source` | string | https URL of the repository |
| `ci_run` | string | https URL of the CI run that built or promoted it |
| `images` | object | service name to `<registry>/<path>@sha256:<64 hex>`; at least one |
| `bundle` | string | the deploy bundle, `<registry>/<path>@sha256:<64 hex>` |
| `schema_version` | string or null | the project's newest migration identifier at this commit; null without a database |
| `rollback` | boolean | true only for a deliberate return to an older release |

Rules:

- Images and the bundle are referenced by digest only; tags never identify what runs.
- A production record is a new record with the same `commit`, `images`, `bundle`, and
  `schema_version` as the staging record, plus `version`. Nothing is rebuilt.
- The engine refuses a record older (`created`) than the one deployed unless `rollback` is
  true.

## 2. Deploy bundle

The files a host needs besides images, taken from the same commit: the Compose file(s),
`deploy/release.toml`, `deploy/settings.toml`, `deploy/env/<environment>.env`, and
`deploy/hooks/`. CI packs them as a tar archive in an OCI artifact (media type
`application/vnd.af.bundle.v1.tar`), pushes it, and signs it. Hosts never receive a
source checkout.

## 3. Release pointer and signatures

- Per environment, the tag `release-<environment>` in the project's image repository
  points at the current release record artifact. Moving the tag is how CI requests a
  deployment; it is the only mutable reference, and the engine resolves it to a digest
  before doing anything.
- CI signs the record, every image, and the bundle by digest with the project's release
  signing key (cosign key pair). The private key lives only in the CI secret store; hosts
  hold the public key. Private repositories sign without the public transparency log:
  cosign 3 signs with `--signing-config` pointing at a config without services
  (`cosign signing-config create --out signing-config.json`), and hosts verify with
  `--insecure-ignore-tlog=true`.
- Before any change, the engine verifies the record signature, then the signature of every
  digest the record names. Any failure refuses the deployment (exit 2).

## 4. Project files

All in the repository, all part of the bundle.

`deploy/release.toml`:

| Key | Meaning |
| --- | --- |
| `af_standard` | `1` |
| `project` | the release record's `project` |
| `compose_files` | Compose files in order |
| `[[images]]` | `name`, `dockerfile`, `context`, `platforms` (default amd64 and arm64); Compose uses `image: ${AF_IMAGE_<NAME>}` |
| `health` | `service`, `path`, `revision_key`, `port_setting`: the engine requests `http://127.0.0.1:<port><path>`, where the placement value `port_setting` is the loopback port; the JSON key `revision_key` must carry the full commit |
| `preflight` | in-image command checking configuration without printing values; `[]` disables |
| `migrate` | one-off command and service; `[]` without a database |
| `schema_version` | how CI finds the newest migration identifier (a glob over migration files) |
| `switch` | `stop-start` (default) or `blue-green` (needs `[[routes]]` and side-by-side safety) |
| `[[routes]]` | `host_setting` and `port_setting`: placement names holding the public domain and the loopback port serving it; on hosts with a `[proxy]` table the engine writes the proxy route after the health check |
| `drain_seconds` | how long running work may take to finish when stopping |
| `[environments.<name>]` | `keep` (retained releases), `backup` (`required` or `optional`) |

`deploy/settings.toml` (the settings manifest): every runtime name appears in exactly one
source.

| Source | Where the value comes from | Example |
| --- | --- | --- |
| default | `deploy/env/<environment>.env`, committed | log level, pool sizes |
| `placement` | the host's `placement.env` (pull), or environment-scoped CI variables (push) | domain, ports, data path, network names |
| `secrets` | files `<base>/secrets/<NAME>` (0600), set once by the operator | database password, provider keys |
| `host_generated` | created once by the engine into `<base>/secrets/`, with `[generators]` | signing keys, session keys |

Also: `required`, `optional`, `pairs`, `[formats]` (full-match, single line),
`[prod_assert]` with `production_environments`. Secrets reach containers as Compose
secrets mounted at `/run/secrets/<name>`, and the application reads `<NAME>_FILE`;
secret values never enter the runtime env file, images, logs, or (in pull mode) CI.

## 5. Engine interface

The engine is the only program that changes a running environment. Sequence:

1. lock (one deployment per project environment);
2. resolve the pointer, verify signatures, refuse an older record unless `rollback`;
3. pull images by digest, unpack the bundle into `releases/<commit>/`;
4. build the runtime file (defaults, placement, `_FILE` references) and check it against
   the settings manifest;
5. preflight inside the image;
6. `pre_migrate` hook;
7. `stop-start`: stop the old release, letting running work finish within
   `drain_seconds`; `blue-green`: the old release keeps serving;
8. `backup` hook (required when the environment says so and the release has a `migrate`
   command), so a `stop-start` backup is taken with nothing writing;
9. migrate in a one-off container;
10. start (`blue-green`: beside the old release); the health check must report the
    record's commit; on hosts with a proxy, write the project's `[[routes]]` file and
    reload the proxy (a refused reload puts the old file back and counts as a failure);
11. `post_switch` hook (`blue-green` moves the route and then stops the old release), then
    the `smoke` hook;
12. promote `current`, write the deployment record, notify, apply retention.

On failure after step 7: if the record's `schema_version` equals the previous release's,
restore the previous release and verify it (exit 3); otherwise leave the project's
services stopped for the owner (exit 5). The engine never restores across a schema
change.

Hooks are executables in `deploy/hooks/` run on the host from the unpacked bundle with
`AF_PROJECT`, `AF_ENVIRONMENT`, `AF_COMMIT`, `AF_VERSION`, `AF_BASE`, `AF_RELEASE_DIR`,
`AF_RUNTIME_FILE`, `AF_SECRETS_DIR`, `AF_COMPOSE_PROJECT`, `AF_IMAGE_<NAME>`, and for
`backup` `AF_BACKUP_DIR` (it must write at least one file). A non-zero exit stops the
deployment.

| Exit | Meaning |
| --- | --- |
| 0 | deployed, or already running this record |
| 1 | failed; nothing to restore |
| 2 | refused: invalid input, signature, configuration, or older record; nothing changed |
| 3 | failed; previous release restored and verified |
| 4 | failed; restore failed too |
| 5 | failed after a schema change; stopped for the owner |
| 75 | another deployment holds the lock |

## 6. Deployment record

`<base>/deployment.json` (latest) and one line per attempt in `<base>/deployments.jsonl`:

| Field | Rule |
| --- | --- |
| `schema` | `af.deployment/v1` |
| `project`, `environment` | as deployed |
| `result` | `deployed`, `unchanged`, `refused`, `failed`, `restored`, `restore_failed`, `stopped`, `busy` |
| `exit_code` | the engine exit code |
| `release` | the record digest (`sha256:<64 hex>`) |
| `commit`, `version`, `images`, `schema_version` | from the record |
| `previous_commit`, `previous_schema_version` | the release replaced, or null |
| `backup` | archive path, or null |
| `trigger` | `pull` or `push` |
| `actor` | the CI actor or `agent` |
| `started`, `finished` | UTC, as in the release record |
| `engine` | engine version |

Records hold identifiers, results, and times only, never settings values or content.

## 7. Host layout

`<DEPLOY_ROOT>/<project>/<environment>/`: `releases/<commit>/` (`record.json`,
`bundle/`, `runtime.env`), `current -> releases/<commit>`, `secrets/` (0700),
`placement.env` (0600), `backups/`, `deployment.json`, `deployments.jsonl`, `lock`.
Owner modes `root` and `self` keep the ownership and permission checks of
`release_host.py`.

## 8. Environments and promotion

- **local:** developers build from the working tree with the same Compose file and
  settings manifest; `setup-local` writes generated secrets; no engine.
- **staging:** every `main` commit that passes CI moves `release-staging`.
- **production:** a protected tag `vX.Y.Z` on a `main` commit creates the production
  record and moves `release-production`, refused unless `release-staging` has pointed at
  that commit. The tag is the owner's statement that staging was accepted; platforms that
  support protected environments or reviewers add that gate as well.
- **rollback:** a manual CI run creates a record for an earlier commit with
  `rollback: true` and moves the pointer; the engine applies the schema rule above.

## 9. Migration rule

Every release must work against the schema left by the release before it (expand, then
contract across two releases). A release that cannot is a breaking release: its notes say
so, and its rollback is a restore from the pre-migration backup, never a redeploy.

## 10. Setting up a project (CI side)

1. Generate the release signing key once: `cosign generate-key-pair`. Store the private key
   and its password as the CI secrets `COSIGN_PRIVATE_KEY` and `COSIGN_PASSWORD`; commit
   only `cosign.pub` as `deploy/release-signing.pub`; delete the local private copy.
2. Copy `assets/deploy/af_publish.py` and `contracts.py` into `deploy/af/` (record the
   framework version in a comment).
3. Write `deploy/release.toml`, `deploy/settings.toml`, `deploy/env/<environment>.env`, and
   any `deploy/hooks/` from the examples in `assets/deploy/contracts/`.
4. GitHub: copy `assets/ci/github/workflows/release.yml` to
   `.github/workflows/af-release.yml` and adapt `release.example.yml`. Protect `v*` tags.
5. Run one publish, promote, and rollback on staging before relying on it; hosts follow
   with the engine (section 11).

## 11. Setting up a host

Linux with systemd, Docker with Compose, Python 3.11 or newer, and cosign.

1. Create the deploy user `af` (`useradd --system --create-home af`) and add it to the
   `docker` group. Docker access is root-equivalent: use the user for deployments only.
2. Copy `af_engine.py`, `af_trigger.py`, `af_oci.py`, `af_release.py`, `af_settings.py`,
   and `contracts.py` from a tagged framework release to `/opt/af/` (root-owned, 0644).
3. Per project and environment: a base `<DEPLOY_ROOT>/<project>/<environment>/` owned by
   `af` (0700), the project's `release-signing.pub` in `/etc/af/keys/`, and the host config
   `/etc/af/hosts/<project>-<environment>.toml` (keys in the `af_engine.py` docstring).
   Write `placement.env` (0600) and set each secret with
   `af_engine.py secret-set --config ... NAME` (value on standard input). Private
   registries need `registry_credentials` with a read-only token.
4. Choose the trigger:
   - **Pull (default):** install `assets/deploy/systemd/af-agent.service` and
     `af-agent.timer` and run `systemctl enable --now af-agent.timer`. Every two minutes
     the agent resolves each host config's pointer and runs the engine when it names a
     release this host has not attempted. A refused release (missing secret, bad
     signature) is retried after 15 minutes or when the pointer moves; a failed, restored,
     or stopped release is not retried until the pointer moves again or the owner runs the
     engine.
   - **Push:** give the project's CI key one line in `~af/.ssh/authorized_keys`:
     `restrict,command="/usr/bin/python3 /opt/af/af_trigger.py receive --config-dir /etc/af/hosts --project <project> --environment <environment>" <public key>`.
     The key can then only request `deploy <environment> [<actor>]` or
     `status <environment>`; no shell, forwarding, or release data. In the repository,
     set `AF_PUSH_SSH_KEY` (secret) and `AF_PUSH_<ENV>_HOST`, `AF_PUSH_<ENV>_KNOWN_HOSTS`
     (variables), and copy `workflows/push-trigger.yml` to `.github/workflows/af-push.yml`.
5. Notifications and health: copy `af_notify.py` and `af_monitor.py` to `/opt/af/` as
   well. Write `/etc/af/notify.toml` (channel kinds `telegram`, `slack`, `discord`,
   `webhook`; format in the `af_notify.py` docstring) with each token or webhook URL in its
   own 0600 file owned by `af`, and add `notify = "/etc/af/notify.toml"` to the host
   configs. The engine notifies every recorded result with its reason, the agent notifies a
   refused release once, and `systemd/af-monitor.timer` (enable like the agent's) checks
   every five minutes that each running release still reports its commit: two failures in
   a row notify `unhealthy`, the next pass notifies `recovered`. Delivery is best effort and
   never changes a deployment.
6. Ingress: install Caddy, use `assets/deploy/caddy/Caddyfile` as `/etc/caddy/Caddyfile`
   (admin API on the unix socket `/run/caddy/admin.sock`, mode 0660), create
   `/etc/caddy/sites/` owned by `af` (0755), add `af` to the `caddy` group, and add a
   `[proxy]` table to the host configs (`sites`, `reload`, `tls`; example in the
   `af_engine.py` docstring). Projects bind their services to `127.0.0.1` and declare
   `[[routes]]`; the engine writes `<sites>/<project>-<environment>.caddy` after the health
   check. With a tunnel in front that terminates TLS, set `tls = false`.
7. Deploy once with `af_engine.py deploy --config ...` (or wait for the agent), then check
   `af_engine.py status --config ...`.
