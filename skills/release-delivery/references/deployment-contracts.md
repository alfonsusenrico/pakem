# Deployment contracts v1

Status: Draft (ADR 0009; becomes Active with deployment standard v1.0)
Applies to: projects with the `deployed` capability on hosts we or our pipeline control
Last reviewed: 2026-10-22
Review by: 2027-04-19
Sources: ADR 0009; owner pipeline study 2026-10-07; deployment research 2026-10-19

ADR 0010 (accepted 2026-10-21) revises the builder side: the builder and trigger are each
project's choice, nothing billed is used without the project's approval, images default
to the host's architecture, and direct delivery (signed source sent over a restricted
SSH command, built on the host) is section 13. Sections 1 to 3 and 10 describe the
registry mode; sections 4 to 9 and 11 apply to both.

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
| `[[images]]` | `name`, `dockerfile` and `context` (relative paths inside the repository, no `..`), `target` (a build stage, default the last one; several images can come from one Dockerfile), `platforms` (default amd64 and arm64); Compose uses `image: ${AF_IMAGE_<NAME>}` |
| `health` | `service`, `path`, `revision_key`, `port`: the engine asks Docker which `127.0.0.1` port publishes the service's container `port` and requests `http://127.0.0.1:<that port><path>` on every running container of the service; the JSON key `revision_key` must carry the full commit |
| `preflight` | in-image command checking configuration without printing values; `[]` disables |
| `migrate` | one-off command and service; `[]` without a database |
| `schema_version` | how CI finds the newest migration identifier (a glob over migration files) |
| `switch` | `stop-start` (default) or `blue-green`: routed services start beside the running ones, the proxy moves to them, then the old ones stop; needs a host `[proxy]`, the health service among the routed services, routed services published on ephemeral loopback ports (`127.0.0.1::<port>`) with no state inside them, and migrations the previous release can run against (section 9) |
| `[[routes]]` | `host_setting` (a placement name holding the public domain), `service`, `port` (its container port); on hosts with a `[proxy]` table the engine points the domain at the loopback ports Docker published for the service's containers |
| `drain_seconds` | how long running work may take to finish when stopping |
| `[environments.<name>]` | `keep` (retained releases), `backup` (`required` or `optional`) |

`deploy/settings.toml` (the settings manifest): every runtime name appears in exactly one
source.

| Source | Where the value comes from | Example |
| --- | --- | --- |
| default | `deploy/env/<environment>.env`, committed | log level, pool sizes |
| `placement` | the host's `placement.env` (pull), or environment-scoped CI variables (push) | domain, ports, data path, network names |
| `secrets` | files `<base>/secrets/<NAME>` (0640, the deploy user and its group), set once by the operator | database password, provider keys |
| `host_generated` | created once by the engine into `<base>/secrets/`, with `[generators]` | signing keys, session keys |

Also: `required`, `optional`, `pairs`, `[formats]` (full-match, single line),
`[prod_assert]` with `production_environments`. Secrets reach containers as Compose
secrets mounted at `/run/secrets/<name>`, and the application reads `<NAME>_FILE`;
secret values never enter the runtime env file, images, logs, or (in pull mode) CI.
Compose mounts file secrets with the host file's owner and mode, so every service that
reads secrets declares `group_add: ["${AF_SECRETS_GID}"]` (the engine sets it to the deploy
user's group) and then reads them as any container user, without root.

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
   `drain_seconds`; `blue-green`: the old release keeps serving (a failure before the
   routes move removes only the new containers and leaves it serving, exit 1);
8. `backup` hook (required when the environment says so and the release has a `migrate`
   command), so a `stop-start` backup is taken with nothing writing;
9. migrate in a one-off container of `migrate_service`, started with its Compose dependencies
   (a first deployment has no database running yet);
10. start (`blue-green`: each routed service beside its running containers, then the
    health check on the new containers, the routes moved to them, and the old containers
    stopped within `drain_seconds`; then the remaining services, such as workers, are
    recreated); the health check must report the record's commit; on hosts with a proxy,
    write the project's `[[routes]]` file and reload the proxy (a refused reload puts the
    old file back and counts as a failure);
11. `post_switch` hook, then the `smoke` hook;
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
| `commit`, `version`, `images`, `schema_version` | from the record (direct releases: images are local image IDs, `sha256:<64 hex>`) |
| `previous_commit`, `previous_schema_version` | the release replaced, or null |
| `backup` | archive path, or null |
| `trigger` | `pull`, `push`, `direct`, or `rollback` (a direct rollback) |
| `actor` | the CI actor, `agent`, or `device:<name>` |
| `started`, `finished` | UTC, as in the release record |
| `engine` | engine version |

Records hold identifiers, results, and times only, never settings values or content.

## 7. Host layout

`<DEPLOY_ROOT>/<project>/<environment>/`: `releases/<commit>/` (`record.json`,
`bundle/`, `runtime.env`), `current -> releases/<commit>`, `secrets/` (0700),
`placement.env` (0600), `backups/`, `deployment.json`, `deployments.jsonl`, `lock`, and
for direct delivery `builds/` (a request's source while it builds, removed afterwards).
Owner modes `root` and `self` keep the ownership and permission checks of
`release_host.py`.

## 8. Environments and promotion

- **local:** developers build from the working tree with the same Compose file and
  settings manifest; `setup-local` writes generated secrets; no engine.
- **staging:** every `main` commit that passes CI moves `release-staging`.
- **production:** a protected tag `vX.Y.Z` on a `main` commit creates the production
  record and moves `release-production`, refused unless `release-staging` has pointed at
  that commit. The tag is the owner's statement that staging was accepted; platforms that
  support protected environments or reviewers add that gate as well. A tag pushed while the
  merge's publish is still running waits for it (`publish_wait_minutes`, default 30), then
  fails if the commit was never published.
- **rollback:** a manual CI run creates a record for an earlier commit with
  `rollback: true` and moves the pointer; the engine applies the schema rule above.

## 9. Migration rule

Every release must work against the schema left by the release before it (expand, then
contract across two releases). With `blue-green` the reverse must hold as well: the
previous release keeps serving after this release's migrations ran, so migrations only
add (columns, tables, indexes) and removals wait for a later release. Switched services
finish their in-flight requests on SIGTERM within `drain_seconds`. A release that cannot is a breaking release: its notes say
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

On GPU hosts, run GPU containers with `runtime: nvidia` (NVIDIA Container Toolkit 1.18+,
CDI) rather than the legacy `deploy.devices` request: any `systemctl daemon-reload`, from
this setup or a package upgrade, strips GPU access from legacy-mode containers until they
are recreated.

`assets/deploy/af_bootstrap.sh` does steps 1, 2, the host parts of 4 and 5, and 6 on Debian
or Ubuntu (amd64, arm64): run `sudo ./af_bootstrap.sh install [--trigger push]
[--deploy-root <path>] [--no-proxy]` from a tagged framework checkout. It keeps an existing
Docker and its containers untouched, refuses a foreign Caddyfile unless
`--replace-caddyfile` (which keeps a backup), and ends with `check`, which can be rerun at
any time. For direct delivery, `pakem host allow-device` does the per-project parts of
step 3 and the keys (section 13); placement values, secrets, and channels stay manual.
For a registry, the per-project parts (step 3, keys, host configs, placement, secrets,
channels, authorized keys) stay manual.

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
   - **Push:** give the project's CI key one line in the deploy user's keys file
     (`/etc/af/authorized_keys`, root-owned; see Device keys below):
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
8. Optional, for private repositories whose checks must stay free (ADR 0010): a
   self-hosted GitHub Actions runner with `/opt/af/af_runner.sh`. `install` creates the
   `ci-runner` user (no sudo; not in `docker`, `af`, or `adm`) with its own rootless Docker
   from the same source as the host's Docker, caps its systemd slice (2 CPUs and 4 GB by
   default), and downloads the checksum-pinned runner. `add-repo <owner>/<repo>` registers
   one repository with a token piped from the owner's GitHub login
   (`gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token --jq .token`);
   `remove-repo` takes a removal token the same way; `check` proves the isolation. Jobs
   use `runs-on: [self-hosted, linux, x64, pakem]`. Job containers reach the host's loopback
   at `10.0.2.2` (a job's own steps already can), and `--network host` means the runner's
   private network, so a test that starts a container publishes its port to `127.0.0.1`
   and runs it as the container's root (the runner user on the host). Never register a public repository:
   anyone could run code on the host through a pull request. A host that serves clients
   does not get a runner.

## 12. Reaching other projects

How a project reaches another project is a project decision, recorded in its `AGENTS.md`;
the address is always a setting (placement or default), never a constant in code.

| Where the other project runs | How | Notes |
|---|---|---|
| Same host | Join its internal Docker network as an external network named by a placement setting | Use the other project's service name (for example `api`). Your own services on that network need project-unique aliases (below) |
| Another host, private | A private network or VPN address | Keep the address in placement; never publish the service to reach it |
| Another deployment, public | Its public URL over TLS, with the other project's authentication | The normal case for projects on different hosts or owners |

On a shared network the other project's service names are visible too, and Docker may
answer a name both networks carry with the other project's container. Reach your own
services there through project-unique aliases (for example `<project>-db` as the database
host) and keep bare names such as `db`, `api`, or `worker` for nothing on a shared
network. Rehearse with a stand-in network that carries the other project's real service
names.

## 13. Direct delivery

ADR 0010, decision 4: no registry. The owner's device sends a signed request for one
commit, and the host builds and deploys it. Commands: `pakem release` (device) and
`assets/deploy/af_receive.py` (host).

**Host configuration.** `/etc/af/hosts/<project>-<environment>.toml` with
`delivery = "direct"` and no `pointer`, `public_key`, or `registry_credentials`; the pull
agent skips it, the push trigger refuses it, and `af_engine.py deploy` refuses it (direct
releases arrive only through the receiver). The monitor checks it like any other.

**Device keys.** One per device (ADR 0010, decision 8), written by
`af_receive.py authorize` (as root, from `pakem host allow-device`):

- `/etc/af/authorized_keys` (root:root 0644):
  `restrict,command="/usr/bin/python3 /opt/af/af_receive.py serve --device <name> --project <p> [--project ...]" ssh-ed25519 <key> pakem-device <name>`;
- `/etc/af/allowed_signers` (root:af 0640): `<p>[,<p>...] namespaces="pakem-release" ssh-ed25519 <key> pakem-device <name>`.

The deploy user owns its home, so anything it runs could rewrite `~af/.ssh/authorized_keys`
or add `authorized_keys2`. The bootstrap therefore moves the keys to the root-owned file
once and adds `/etc/ssh/sshd_config.d/pakem-user.conf` (`Match User af`,
`AuthorizedKeysFile /etc/af/authorized_keys`). It keeps the change only when `sshd -t`
passes and `sshd -T` shows the new file; otherwise (sshd reads no drop-ins) the keys stay
in `~af/.ssh`, and `check` fails "deploy user keys". The receiver writes whichever file
exists. Which devices exist comes from `allowed_signers`; a device line in the keys file
that is not there is dropped on the next change. Other lines in either file are kept as
they are. A key belongs to one device name, and a device to one key; a shared builder (a
CI runner, a client platform) gets one key per project, named `builder-<project>`
(`pakem host allow-device --device builder-<project> --key FILE`). Revoking removes the
device's lines or one project from them.

The forced command is OpenSSH's; an SSH server that does not read `authorized_keys`
(Tailscale SSH answers port 22 of a tailnet address and logs in by tailnet identity) gives
the key a shell instead. Such a host gets a release port: `af_bootstrap.sh
--release-port PORT` adds `/etc/ssh/sshd_config.d/pakem-release.conf`, where OpenSSH also
listens on PORT with `AllowUsers af` and public keys only, keeping every port it listened
on before; devices connect there. A port sshd already serves is refused (other than the
earlier release port), since only `af` could log in on it afterwards. The release port has
no `ListenAddress` of its own, so it listens wherever sshd does (every interface by
default): on a host with a public or LAN address, firewall it to the private network the
devices use.

**Request.** One JSON object, signed with `ssh-keygen -Y sign -n pakem-release` by the
device key:

| Field | Rule |
| --- | --- |
| `schema` | `pakem.release-request/v1` |
| `action` | `publish` (never to production), `promote` (only to production), or `rollback` |
| `project`, `environment`, `commit` | as in section 1 |
| `version` | `vMAJOR.MINOR.PATCH` for `promote`, else null |
| `archive` | `sha256:<64 hex>` of the commit's `git archive` tar when it is sent; required for `publish` |
| `created` | UTC time; the host refuses a request older than 10 minutes or more than 2 minutes ahead of its clock |
| `device` | the device name, `[a-z0-9][a-z0-9-]{0,62}` |
| `source` | https URL of the repository, or null |
| `allow_schema` | true only for a rollback the owner confirmed across a schema change |

`SSH_ORIGINAL_COMMAND` is `release` (standard input: the request on one line, its
signature base64-encoded on the next, then the archive if named), `history <project>`,
or `status <project>` (JSON per environment: `current` deployment record, `running`
commit, and for history every deployment and the `kept` commits). Anything else is
refused. The receiver checks, before reading the archive's content: the schema; the
device and project against its forced command; freshness; the signature against only
this device's `allowed_signers` lines with the project as principal; and that the
environment is direct here. Then, under the environment's deployment lock (a second
release for it is `busy`, exit 75, before anything is built), that this exact request has
not run here while fresh (each signed request runs once), and the archive's digest
against the request. A build that fails, or takes longer than its timeout, sends a
`failed` notice; it writes no deployment record, since nothing was released.

**Build.** A commit no environment of the project keeps is unpacked (files,
directories, and symlinks that stay inside; size and entry limits), its deploy files
validated, and each `[[images]]` entry built with `docker buildx build --load` in the
`pakem-build` BuildKit container (2 CPUs, 4 GB; one build per host at a time; cache older
than 7 days pruned), with `AF_REVISION` and the revision label as in CI, tagged
`pakem/<project>-<image>:<commit>`. The deploy bundle (section 2) is taken from the same
tree. A kept commit (promotion, rollback, or the same commit again) reuses its images
and bundle; a resent archive must match the one it was built from.

**Record.** `releases/<commit>/record.json` is a direct release record:

| Field | Rule |
| --- | --- |
| `schema` | `af.direct-release/v1` |
| `project`, `commit`, `created`, `schema_version`, `rollback` | as in section 1 |
| `version` | the promotion's version; a rollback keeps the version it had in that environment |
| `source`, `device` | from the request |
| `archive` | the digest of the archive the images were built from |
| `images` | image name to local image ID `sha256:<64 hex>` (Compose runs images by ID) |
| `request` | `sha256:<64 hex>` of the signed request; also the deployment record's `release` |

The engine (section 5) then runs as usual, except that it checks the images exist
instead of pulling them; a request for the release that already runs is `unchanged`.
An environment switched from the registry may republish the commit it runs; afterwards
the pulled images are removed, as retention removes those of older registry releases.

**Rules.** `promote` to production is refused unless staging on this host deployed the
commit (when the project has staging), and when its version already named another commit
in production. `rollback` is refused for a commit never deployed to that environment,
for the running commit, and across a schema version change without `allow_schema`; its
deployment record has trigger `rollback`, so "previous" never returns to a release a
rollback left.
Retention untags a release's images unless another environment of the project keeps
the same commit; Docker keeps images a container still uses.
