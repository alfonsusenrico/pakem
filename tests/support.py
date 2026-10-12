"""Shared helpers for the af test suite.

Every command runs in a subprocess with HOME pointing at a temporary directory,
so tests never touch the real home directory or global git configuration.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AF = REPO_ROOT / "tools" / "af"
GUARD = REPO_ROOT / "enforcement" / "guard.py"
HOOKS_DIR = REPO_ROOT / "hooks"
CHECK_SCRIPT = REPO_ROOT / "tools" / "check_pinned_blocks.sh"

DEPLOY_ASSETS = REPO_ROOT / "skills" / "release-delivery" / "assets" / "deploy"

FIXTURE_VERSION = "0.2.0"

FIXTURE_KERNEL = textwrap.dedent(
    """\
    # Kernel

    Always verify before claiming success.
    Never print secret values.

    ## Defaults

    - `default:commit-style` Subject-only conventional commits.
    - `default:branching` Work on topic branches.
    """
)

FIXTURE_DIGEST = textwrap.dedent(
    """\
    <!-- digest of the kernel -->
    Always verify before claiming success.
    Never print secret values.
    """
)

FIXTURE_SKILL = textwrap.dedent(
    """\
    ---
    name: demo-skill
    description: Demonstrates a valid skill for tests.
    metadata:
      owner: "tests"
    ---
    # Demo skill

    See [the guide](references/guide.md).
    """
)

FIXTURE_REFERENCE = textwrap.dedent(
    """\
    # Guide

    Status: active
    Last reviewed: 2026-01-01
    Review by: 2999-01-01

    Body text.
    """
)

FIXTURE_CAPABILITIES = textwrap.dedent(
    """\
    [capabilities.always]
    summary = "Every project"
    standards = ["formatter, linter, and CI"]

    [capabilities.database]
    summary = "Uses a database"
    standards = ["real disposable Postgres in tests and CI"]

    [capabilities.deployed]
    summary = "Used by other people"
    standards = ["security scans in CI"]
    """
)


def targets_toml(extra_codex_skills_dir: bool = False, claude_max_chars: int = 40000) -> str:
    codex_dirs = '["~/.agents/skills"]'
    if extra_codex_skills_dir:
        codex_dirs = '["~/.agents/skills", "~/.codex/skills"]'
    return textwrap.dedent(
        f"""\
        [hosts.claude]
        label = "Claude Code"
        instructions = "~/.claude/CLAUDE.md"
        skills_dirs = ["~/.claude/skills"]
        max_chars = {claude_max_chars}

        [hosts.codex]
        label = "Codex"
        instructions = "~/.codex/AGENTS.md"
        skills_dirs = {codex_dirs}
        max_bytes = 32768

        [hosts.antigravity]
        label = "Antigravity"
        instructions = "~/.gemini/GEMINI.md"
        skills_dirs = ["~/.gemini/config/skills"]
        max_bytes = 24000
        must_not_exist = ["~/.gemini/AGENTS.md"]
        """
    )


def load_deploy_script(name: str):
    """Import a deploy asset script (for example release_host.py) as a module."""
    path = DEPLOY_ASSETS / name
    spec = importlib.util.spec_from_file_location(f"af_deploy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)
    return path


def isolated_env(home: Path, extra: dict | None = None) -> dict:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    if extra:
        env.update(extra)
    return env


def git(
    repo: Path,
    *args: str,
    home: Path,
    check: bool = True,
    extra_env: dict | None = None,
    input_text: str | None = None,
):
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=isolated_env(home, extra_env),
        input=input_text,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed


def init_repo(path: Path, home: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", branch, home=home)
    git(path, "config", "user.name", "Test", home=home)
    git(path, "config", "user.email", "test@example.invalid", home=home)
    git(path, "config", "commit.gpgsign", "false", home=home)
    return path


def activate_hooks(repo: Path, home: Path) -> Path:
    """Mark the framework hooks active, as af hooks install does; af project check warns otherwise."""
    write(repo / ".githooks" / "pre-push", "#!/bin/sh\nexit 0\n")
    git(repo, "config", "core.hooksPath", ".githooks", home=home)
    return repo


class IsolatedTestCase(unittest.TestCase):
    """Provides a temporary HOME and a fixture framework checkout per test."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="af-test-")
        self.base = Path(self._temporary.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.framework = self.base / "framework"
        self.build_framework(self.framework)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def build_framework(self, root: Path, **target_options) -> Path:
        write(root / "VERSION", FIXTURE_VERSION + "\n")
        write(root / "kernel" / "KERNEL.md", FIXTURE_KERNEL)
        write(root / "kernel" / "DIGEST.md", FIXTURE_DIGEST)
        write(root / "hosts" / "targets.toml", targets_toml(**target_options))
        write(root / "skills" / "demo-skill" / "SKILL.md", FIXTURE_SKILL)
        write(root / "skills" / "demo-skill" / "references" / "guide.md", FIXTURE_REFERENCE)
        write(root / "lint" / "banned.toml", (REPO_ROOT / "lint" / "banned.toml").read_text())
        write(root / "standards" / "capabilities.toml", FIXTURE_CAPABILITIES)
        # The project template ships inside the project-bootstrap skill, so the fixture
        # needs that skill to be valid as well.
        write(
            root / "skills" / "project-bootstrap" / "SKILL.md",
            "---\nname: project-bootstrap\ndescription: Use when bootstrapping a fixture project.\n---\n\n# Bootstrap\n",
        )
        write(
            root / "skills" / "project-bootstrap" / "assets" / "project" / "AGENTS.md.tmpl",
            "# {{project_name}}\n\nProject notes.\n\nCapabilities: none\n",
        )
        return root

    def af(
        self, *args: str, root: Path | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        command = [sys.executable, str(AF), "--root", str(root or self.framework), *args]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=isolated_env(self.home),
            cwd=str(cwd or self.base),
        )

    def assert_ok(self, completed: subprocess.CompletedProcess) -> None:
        if completed.returncode != 0:
            self.fail(
                f"exit {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

    def assert_fails(self, completed: subprocess.CompletedProcess, code: int = 1) -> None:
        if completed.returncode != code:
            self.fail(
                f"expected exit {code}, got {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

    def snapshot(self, directory: Path) -> dict[str, str]:
        result = {}
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory).as_posix()
            if path.is_symlink():
                result[relative] = "link:" + os.readlink(path)
            elif path.is_file():
                result[relative] = path.read_text(encoding="utf-8", errors="replace")
            else:
                result[relative] = "dir"
        return result
