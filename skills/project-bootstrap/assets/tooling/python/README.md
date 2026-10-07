# Python tooling

Default formatter, linter, type checker, and test runner settings for Python projects
(Working Standard Profile, sections 2 and 9). The CI templates in the release-delivery skill (`assets/ci/`) run the
same commands.

## Adopt

1. Merge the tables from `pyproject.fragment.toml` into the project's `pyproject.toml`.
   Keep `[project] requires-python` accurate: Ruff infers its target version from it.
2. Add the tools as project-local development dependencies (lockfile updated):

   ```sh
   uv add --dev ruff pyright pytest
   ```

3. Copy `../.editorconfig` to the repository root if the project has none.
4. Run the commands below once and fix or explicitly ignore what they report before
   enabling CI.

## Commands

| Purpose | Command |
| --- | --- |
| Install exactly the lockfile | `uv sync --locked` |
| Format | `uv run ruff format` |
| Format check (CI) | `uv run ruff format --check` |
| Lint | `uv run ruff check` (add `--fix` locally for safe fixes) |
| Type check | `uv run pyright` |
| Tests | `uv run pytest` |

## Choices

- Ruff rule set `E, F, W, I, B, UP, S, SIM, RUF`; `E501` is left to the formatter. Tests may
  use `assert` and fixture credentials for disposable services (`S101`, `S105`, `S106`).
- Pyright `standard` mode. Raise to `strict` for new code with typed dependencies, or per
  package through `strict = [...]` once that package is clean.
- Pytest runs with `--strict-markers` and `--strict-config`, so a mistyped marker or option
  fails instead of being ignored, and `xfail_strict` makes an unexpected pass fail.

## Database tests (`database` capability)

Tests that need Postgres are marked `@pytest.mark.db`. CI sets `REQUIRE_DB=1` and
`TEST_DATABASE_URL`; with `REQUIRE_DB=1` a missing database must fail the run, never skip
it. A minimal `conftest.py` contract:

```python
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TEST_DATABASE_URL"):
        return
    if os.environ.get("REQUIRE_DB") == "1":
        raise pytest.UsageError("REQUIRE_DB=1 but TEST_DATABASE_URL is not set")
    skip_db = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
```
