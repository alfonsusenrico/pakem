# Tooling Baseline

Status: Active
Applies to: every repository from the first commit
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner Working Standard Profile sections 4 and 7

| Stack | Package manager | Format | Lint | Types | Tests |
| --- | --- | --- | --- | --- | --- |
| Python | uv (`uv.lock`, `uv sync --locked` in CI) | `ruff format` | `ruff check` | pyright (standard mode) | pytest |
| TypeScript / JavaScript | npm (`package-lock.json`, `npm ci`) unless the project chose otherwise | Prettier (defaults) | ESLint flat config | `tsc --noEmit` (strict) | Vitest or the framework's runner |
| Shell | - | - | shellcheck | - | - |
| Dockerfile / compose | - | - | hadolint, `docker compose config` | - | - |

- One lockfile per package manager; never two (no `package-lock.json` and
  `yarn.lock` together).
- Each check runs with one documented command (`make` targets, `npm run`, or `uv run`)
  and the same commands run in CI.
- Pin runtime versions (`.python-version`, `engines`/`.nvmrc`) and keep CI and
  Dockerfiles on the same versions.
- Configuration fragments for these tools ship in this skill's `assets/tooling/`
  (Python `pyproject` fragment, Prettier, ESLint, EditorConfig, each with a README);
  CI templates ship with the `release-delivery` skill.
