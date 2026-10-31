"""pakem health: findings, tiers, protection rules, and clean's re-verification (ADR 0016)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import PAKEM, REPO_ROOT, IsolatedTestCase, isolated_env, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
from pakem_lib import health  # noqa: E402

OLD = time.time() - 5 * 86_400


def make_rust_project(root: Path) -> Path:
    project = root / "app"
    write(project / "Cargo.toml", "[package]\nname = 'app'\n")
    stale = write(project / "target" / "debug" / "incremental" / "app-old" / "s-1" / "work.bin", "x" * 50_000).parent.parent
    write(project / "target" / "debug" / "incremental" / "app-new" / "s-2" / "work.bin", "y" * 50_000)
    write(project / "target" / "debug" / "deps" / "libapp.rlib", "z" * 50_000)
    cross = write(project / "target" / "aarch64-linux-android" / "debug" / "out.bin", "a" * 50_000).parent.parent
    for path in (stale, cross):
        os.utime(path, (OLD, OLD))
    write(project / "package.json", "{}\n")
    write(project / "node_modules" / "left-pad" / "index.js", "module.exports = 1\n")
    return project


class FindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.project = make_rust_project(self.root)
        self.settings = dict(health.DEFAULTS)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def kinds(self, findings) -> dict:
        return {finding.kind: finding for finding in findings}

    def test_tiers_and_what_is_never_listed(self) -> None:
        findings = self.kinds(health.build_findings([self.root], self.settings, []))
        incremental = findings["rust-incremental"]
        self.assertEqual(incremental.tier, health.ZERO)
        self.assertEqual([Path(path).name for path in incremental.paths], ["app-old"], "young folders are kept")
        self.assertEqual(findings["rust-cross-target"].tier, health.WARM)
        self.assertEqual(findings["build-node_modules"].tier, health.WARM)
        listed = [path for finding in findings.values() for path in finding.paths]
        self.assertFalse(any("deps" in path for path in listed), "target/*/deps is never a finding")

    def test_a_compiler_in_the_project_marks_rust_findings_in_use(self) -> None:
        cargo = health.Process(1, "/usr/bin/cargo build", str(self.project))
        findings = self.kinds(health.build_findings([self.root], self.settings, [cargo]))
        self.assertTrue(findings["rust-incremental"].in_use)
        self.assertTrue(findings["build-node_modules"].in_use, "a build in the project holds its dependencies")

    def test_an_idle_agent_session_does_not_block_stale_incremental(self) -> None:
        agent = health.Process(2, "~/.local/bin/claude --resume x", str(self.project))
        findings = self.kinds(health.build_findings([self.root], self.settings, [agent]))
        self.assertFalse(findings["rust-incremental"].in_use)
        self.assertTrue(findings["build-node_modules"].in_use)

    def test_an_agent_in_a_git_worktree_root_protects_subpackage_builds(self) -> None:
        repo = self.root / "monorepo"
        write(repo / ".git" / "HEAD", "ref: refs/heads/main\n")
        write(repo / "frontend" / "package.json", "{}\n")
        write(repo / "frontend" / "node_modules" / "pkg" / "index.js", "1\n")
        agent = health.Process(3, "~/.local/bin/claude --resume y", str(repo))
        findings = [f for f in health.build_findings([self.root], self.settings, [agent])
                    if f.project == str(repo / "frontend")]
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].in_use, "an agent at the worktree root holds subpackage build output")
        self.assertFalse(health.is_builder(health.Process(4, "/System/Library/UVCAssistant", "/")))
        self.assertFalse(health.is_agent(health.Process(5, "~/.codex/bin/codex app-server --listen unix", "/")))

    def test_sizes_are_allocated_blocks_not_apparent_size(self) -> None:
        sparse = self.root / "sparse.raw"
        with sparse.open("wb") as handle:
            handle.seek(512 * 1024 * 1024)
            handle.write(b"x")
        self.assertLess(health.allocated(sparse), sparse.stat().st_size // 100)

    def test_named_volumes_are_never_findings(self) -> None:
        anonymous_free, anonymous_used = "a" * 64, "b" * 64
        state = health.DockerState(available=True, named_volumes=["shop_db_data"],
                                   anonymous_volumes=[anonymous_free, anonymous_used], mounted={anonymous_used},
                                   volume_sizes={anonymous_free: 1000, anonymous_used: 2000},
                                   images=[{"id": "sha256:1", "name": "<none>:<none>", "size": 10, "dangling": True},
                                           {"id": "sha256:2", "name": "postgres:16", "size": 20, "dangling": False},
                                           {"id": "sha256:3", "name": "app:latest", "size": 30, "dangling": False}],
                                   used_images={"sha256:3"})
        findings = self.kinds(health.docker_findings(state, self.settings))
        self.assertEqual(findings["docker-volumes"].paths, [anonymous_free])
        self.assertEqual(findings["docker-image-dangling"].tier, health.ZERO)
        self.assertEqual(findings["docker-image"].docker, "sha256:2", "an image a container uses is not listed")
        self.assertNotIn("shop_db_data", json.dumps([finding.paths for finding in findings.values()]))

    def test_clean_refuses_what_changed_since_the_check(self) -> None:
        finding = {"kind": "rust-incremental", "paths": [str(self.project / "target/debug/incremental/app-old")],
                   "project": str(self.project)}
        self.assertIsNone(health._verify(finding, [self.root], [], None))
        cargo = health.Process(1, "cargo test", str(self.project))
        self.assertIn("cargo or rustc", health._verify(finding, [self.root], [cargo], None))
        elsewhere = Path(self._temporary.name) / "other"
        self.assertIn("outside the workspace roots", health._verify(finding, [elsewhere], [], None))
        volumes = {"kind": "docker-volumes", "paths": ["shop_db_data"]}
        self.assertIn("only anonymous volumes", health._verify(volumes, [self.root], [], health.DockerState(available=True)))

    def test_amounts_and_sizes_parse(self) -> None:
        self.assertEqual(health.parse_size("4.14GB"), 4_140_000_000)
        self.assertEqual(health.parse_size("0B"), 0)
        self.assertEqual(health.parse_size("332.4MB"), 332_400_000)


class HealthCommandTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.base / "work"
        self.project = make_rust_project(self.root)
        write(self.home / ".config" / "pakem" / "health.toml", f'roots = ["{self.root}"]\n')
        # Only system tools and a docker that always fails: a test never reaches the real Docker or Homebrew.
        fake_bin = self.base / "fake-bin"
        write(fake_bin / "docker", "#!/bin/sh\nexit 1\n").chmod(0o755)
        self.path = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"

    def health(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(PAKEM), "--root", str(self.framework), "health", *args],
                              capture_output=True, text=True, env=isolated_env(self.home, {"PATH": self.path}),
                              cwd=str(self.base), timeout=300)

    def test_check_then_clean_removes_only_the_approved_finding(self) -> None:
        checked = self.health("check", "--json")
        self.assert_ok(checked)
        result = json.loads(checked.stdout)
        self.assertIn(result["disk"]["urgency"], ("critical", "watch", "healthy"))
        by_kind = {finding["kind"]: finding for finding in result["findings"]}
        incremental = by_kind["rust-incremental"]

        planned = self.health("clean", incremental["id"], "--plan")
        self.assert_ok(planned)
        self.assertTrue((self.project / "target/debug/incremental/app-old").exists(), "--plan removes nothing")

        cleaned = self.health("clean", incremental["id"], "--yes")
        self.assert_ok(cleaned)
        self.assertIn("removed", cleaned.stdout)
        self.assertFalse((self.project / "target/debug/incremental/app-old").exists())
        self.assertTrue((self.project / "target/debug/incremental/app-new").exists())
        self.assertTrue((self.project / "node_modules").exists(), "findings not named are untouched")
        self.assertTrue((self.project / "target/debug/deps").exists())

    def test_clean_refuses_ids_not_in_the_last_check(self) -> None:
        self.assert_ok(self.health("check", "--json"))
        refused = self.health("clean", "deadbeef", "--yes")
        self.assert_fails(refused, 6)
        self.assertIn("not in the last check", refused.stderr)

    def test_clean_without_a_check_is_refused(self) -> None:
        refused = self.health("clean", "deadbeef", "--yes")
        self.assert_fails(refused, 6)
        self.assertIn("Run `pakem health check` first", refused.stderr)


class ReportSummaryTests(IsolatedTestCase):
    def report_text(self) -> str:
        config = write(self.base / "report.toml", "repos_dirs = []\n")
        output = self.base / "reports"
        self.assert_ok(self.pakem("report", "--config", str(config), "--output-dir", str(output)))
        return (output / "latest.md").read_text()

    def test_the_weekly_report_summarises_health_once_configured(self) -> None:
        self.assertNotIn("Device health", self.report_text(), "no section before the device configures health")
        # Every device is critical at 100%: the summary must show as a warning, and stay read-only.
        write(self.home / ".config" / "pakem" / "health.toml", "roots = []\ncritical_free_percent = 100\n")
        text = self.report_text()
        self.assertIn("## Device health", text)
        self.assertIn("critical. Details and Docker: `pakem health check`", text)


if __name__ == "__main__":
    unittest.main()
