# CI template pins

Resolved: 2026-10-07 (deploy entries: 2026-10-08). Every third-party action, tool binary,
and image used by `assets/ci/` is listed here; `tests/test_ci_templates.py` fails when a template uses an
action SHA or tool checksum that is missing from this file.

## GitHub Actions (full commit SHA)

Resolved with `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag> 'refs/tags/<tag>^{}'`;
all four tags are lightweight (no `^{}` line). The first three SHAs were confirmed as commits
through the GitHub commits API; the Tailscale SHA by fetching it and `git cat-file -t`.

| Action | Tag | Commit SHA | Notes |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | node24 runtime; self-hosted runners need Actions Runner v2.327.1 or later. v7 refuses fork checkouts under `pull_request_target`/`workflow_run`. |
| `astral-sh/setup-uv` | `v10.2.0` | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | v10 disables `enable-cache: auto` for `pull_request_target`, `workflow_run`, and `release` events. |
| `actions/setup-node` | `v7.0.0` | `820762786026740c76f36085b0efc47a31fe5020` | v7 is an ESM migration only; npm caching is explicit in the templates. |
| `tailscale/github-action` | `v4.2.0` | `d1b6cd204f8dceda5b3eaad7f1f767be390056cd` | deploy.yml, push-trigger.yml; node24 runtime; inputs `oauth-client-id`, `oauth-secret`, `tags` (OAuth client needs the `auth_keys` scope); installs Tailscale 1.94.2 by default. |

Not used: `gitleaks/gitleaks-action` (v3.0.0 latest) requires a paid `GITLEAKS_LICENSE` for
organization repositories (its README, "Environment Variables"), so the templates run the
MIT-licensed gitleaks CLI instead.

## Tool binaries (GitHub workflows, linux x86_64)

Checksums are the GitHub release asset digests, cross-checked against each project's
published checksum file and a local download.

| Tool | Version | Asset | SHA-256 |
| --- | --- | --- | --- |
| gitleaks | `8.30.1` | `gitleaks_8.30.1_linux_x64.tar.gz` | `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` |
| actionlint | `1.7.12` | `actionlint_1.7.12_linux_amd64.tar.gz` | `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8` |
| shellcheck | `0.11.0` | `shellcheck-v0.11.0.linux.x86_64.tar.xz` | `8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198` |
| hadolint | `2.15.1` | `hadolint-linux-x86_64` | `c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507` |

| cosign | `v3.1.3` | `cosign-linux-amd64` | `4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71` |
| oras | `1.3.5` | `oras_1.3.5_linux_amd64.tar.gz` | `f48a73ea6d026a862c3919fc773da9a8c86fc259c2c737d5baed3b8b9614ad22` |

cosign and oras (release.yml, resolved 2026-10-19) were cross-checked against
`cosign_checksums.txt` and `oras_1.3.5_checksums.txt` from the same releases.

## Tools run through uv or npm

| Tool | Version | Where |
| --- | --- | --- |
| uv | `0.12.23` | `uv_version` input default; GitLab uv image tag |
| pip-audit | `2.10.1` | `uvx pip-audit@<version>` in both security templates |
| Node.js | `24` (GitHub input), `24.21.0` (GitLab image) | verify-node, security, database |

## Container images

Tags are pinned to exact versions in the GitLab templates. The digests below are the
multi-architecture index digests on 2026-10-07; append `@sha256:<digest>` to the tag to pin
by digest, and refresh both together.

| Image | Used by | Digest |
| --- | --- | --- |
| `ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim` | GitLab verify-python, python-audit, database | `sha256:a6aeb5c166af9f765f9c68e585b5a5148c28f3b8a362f90151cecea88b21a3e2` |
| `node:24.21.0-trixie-slim` | GitLab verify-node, node-audit | `sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66` |
| `postgres:17.9-trixie` | GitLab database service | `sha256:2a0d0fe14825b0939f78a8cad5cd4e6aa68bf94d0e5dd96e24b6d23af4315545` |
| `postgres:17` | GitHub database default (floating major tag) | `sha256:ae69c452f483507a6b99fb654cf93aad7fe156ffd2c56247707eef4e36d3c12b` |
| `ghcr.io/gitleaks/gitleaks:v8.30.1` | GitLab secret-scan | `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` |
| `koalaman/shellcheck-alpine:v0.11.0` | GitLab shellcheck | `sha256:9955be09ea7f0dbf7ae942ac1f2094355bb30d96fffba0ec09f5432207544002` |
| `hadolint/hadolint:v2.15.1-debian` | GitLab hadolint | `sha256:9a3944b7fddcb947d1ffd90829ac1a6e5c30479223358f249d8b96c7d0019e27` |
| `python:3.13.16-alpine3.24` | GitLab deploy, rollback, smoke (adds `git`, `openssh-client-default`, `openssh-keygen` with apk at job start) | `sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a` |

Used only by the opt-in deploy sandbox test (`tests/test_deploy_sandbox.py`), pinned by
digest in its fixtures:

| Image | Used by | Digest |
| --- | --- | --- |
| `tonistiigi/binfmt:qemu-v10.2.3` | GitHub release (arm64 emulation for multi-architecture builds; resolved 2026-10-19) | `sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0` |
| `moby/buildkit:v0.33.1` | GitHub release (buildx builder; v0.34.0 was 12 days old on 2026-10-19) | `sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea` |
| `docker:29.4.3-dind` | sandbox host (sshd, Docker in Docker) | `sha256:685b91dca8eab7de1dce1c303dbb7a763e4082d6a60db10968adf3295fbd2495` |
| `busybox:1.37.0` | sandbox sample app | `sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e` |

## Updating

1. Resolve the new tag's commit with `git ls-remote` (dereference annotated tags with `^{}`),
   or the new release asset's SHA-256 from its published checksum file.
2. Read the release notes for breaking changes and runner requirements.
3. Update the template, this file, and the date above together; run
   `python3 -m unittest discover -s tests` and actionlint on the GitHub templates.
4. Dependabot or Renovate in each project can propose action and image bumps; they keep the
   SHA-plus-comment form.
