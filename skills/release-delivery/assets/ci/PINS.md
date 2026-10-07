# CI template pins

Resolved: 2026-10-07. Every third-party action, tool binary, and image used by
`assets/ci/` is listed here; `tests/test_ci_templates.py` fails when a template uses an
action SHA or tool checksum that is missing from this file.

## GitHub Actions (full commit SHA)

Resolved with `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag> 'refs/tags/<tag>^{}'`;
all three tags are lightweight (no `^{}` line), and each SHA was confirmed as a commit through
the GitHub commits API.

| Action | Tag | Commit SHA | Notes |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | node24 runtime; self-hosted runners need Actions Runner v2.327.1 or later. v7 refuses fork checkouts under `pull_request_target`/`workflow_run`. |
| `astral-sh/setup-uv` | `v10.2.0` | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | v10 disables `enable-cache: auto` for `pull_request_target`, `workflow_run`, and `release` events. |
| `actions/setup-node` | `v7.0.0` | `820762786026740c76f36085b0efc47a31fe5020` | v7 is an ESM migration only; npm caching is explicit in the templates. |

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

## Updating

1. Resolve the new tag's commit with `git ls-remote` (dereference annotated tags with `^{}`),
   or the new release asset's SHA-256 from its published checksum file.
2. Read the release notes for breaking changes and runner requirements.
3. Update the template, this file, and the date above together; run
   `python3 -m unittest discover -s tests` and actionlint on the GitHub templates.
4. Dependabot or Renovate in each project can propose action and image bumps; they keep the
   SHA-plus-comment form.
