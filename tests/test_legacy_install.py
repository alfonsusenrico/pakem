"""Migration of a device installed under the former product name, agent-framework.

The migration runs in-process with HOME pointing at a temporary directory and an
injected launchctl runner, so no real launchd job is touched; the CLI tests run
`pakem install` and `pakem enforce` on a device without a recorded launchd job.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import REPO_ROOT, IsolatedTestCase, write

sys.path.insert(0, str(REPO_ROOT / "tools"))

from pakem_lib import enforce, legacy_install  # noqa: E402
from pakem_lib.common import Framework, sha256_file  # noqa: E402

UID = 501
PYTHON = "/usr/local/bin/python3-for-tests"
LEGACY_LABEL = "com.agentframework.drift-report"
LABEL = "com.pakem.drift-report"
FORMER_GUARD = "# guard.py as the former version installed it\n"
FORMER_RULES = "# codex rules as the former version installed them\n"
FORMER_PLIST = "<?xml version='1.0'?><plist><dict><key>Label</key><string>former</string></dict></plist>\n"
BACKUP_PATH = "backups/20261001T000000Z/.claude/settings.json"
USER_SETTINGS = {
    "model": "opus",
    "env": {"EDITOR": "vim"},
    "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}]},
}


def former(value: dict) -> dict:
    """A current enforcement fragment as the former version shipped it."""
    text = json.dumps(value)
    text = text.replace("/.local/share/pakem/", "/.local/share/agent-framework/")
    text = text.replace('"pakem-guard"', '"agent-framework-guard"')
    return json.loads(text)


class FakeLaunchctl:
    """Records launchctl arguments and answers with configured exit codes."""

    def __init__(self, returncodes: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncodes = returncodes or {}

    def __call__(self, arguments: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(arguments))
        code = self.returncodes.get(arguments[0], 0)
        return subprocess.CompletedProcess(["launchctl", *arguments], code, stdout="", stderr="" if code == 0 else "failed")


class LegacyDeviceTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.launchctl = FakeLaunchctl()
        self.legacy_state = self.home / ".local" / "state" / "agent-framework"
        self.legacy_share = self.home / ".local" / "share" / "agent-framework"
        self.state = self.home / ".local" / "state" / "pakem"
        self.share = self.home / ".local" / "share" / "pakem"
        self.claude_settings = self.home / ".claude" / "settings.json"
        self.antigravity_hooks = self.home / ".gemini" / "config" / "hooks.json"
        self.legacy_rules = self.home / ".codex" / "rules" / "agent-framework.rules"
        self.rules = self.home / ".codex" / "rules" / "pakem.rules"
        self.legacy_plist = self.home / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
        self.plist = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def build_framework(self, root, **target_options):
        root = super().build_framework(root, **target_options)
        shutil.copytree(REPO_ROOT / "enforcement", root / "enforcement", dirs_exist_ok=True)
        return root

    def fragment(self, relative: str) -> dict:
        return json.loads((self.framework / "enforcement" / relative).read_text())

    def make_legacy_device(self, with_schedule: bool = True) -> None:
        """State as the former version left it after install, enforce, and schedule."""
        manifest = {
            "schema": 1,
            "framework_version": "1.3.0",
            "installed_at": "2026-10-01T00:00:00+00:00",
            "entries": [],
            "backups": [
                {
                    "original": "~/.claude/settings.json",
                    "timestamp": "20261001T000000Z",
                    "reason": "enforce-claude",
                    "backup_path": f"~/.local/state/agent-framework/{BACKUP_PATH}",
                }
            ],
            "enforcement": {},
            "schedule": {},
        }
        write(self.legacy_state / BACKUP_PATH, "{}\n")
        write(self.legacy_state / "reports" / "latest.md", "# report\n")

        guard = self.legacy_share / "guard.py"
        write(guard, FORMER_GUARD)
        manifest["enforcement"]["guard"] = {
            "path": "~/.local/share/agent-framework/guard.py",
            "sha256": sha256_file(guard),
            "users": ["antigravity", "claude"],
        }
        for host, relative, target, existing in (
            ("claude", "claude/settings.fragment.json", self.claude_settings, USER_SETTINGS),
            ("antigravity", "antigravity/hooks.fragment.json", self.antigravity_hooks, None),
        ):
            data = json.loads(json.dumps(existing)) if existing else {}
            changes = enforce.merge_fragment(data, former(self.fragment(relative)))
            write(target, enforce.render_json(data))
            manifest["enforcement"][host] = {
                "target": "~/" + target.relative_to(self.home).as_posix(),
                "created_file": existing is None,
                "changes": changes,
                "installed_at": "2026-10-01T00:00:00+00:00",
            }

        write(self.legacy_rules, FORMER_RULES)
        manifest["enforcement"]["codex"] = {
            "target": "~/.codex/rules/agent-framework.rules",
            "sha256": sha256_file(self.legacy_rules),
            "installed_at": "2026-10-01T00:00:00+00:00",
        }
        if with_schedule:
            write(self.legacy_plist, FORMER_PLIST)
            manifest["schedule"][LEGACY_LABEL] = {
                "plist": f"~/Library/LaunchAgents/{LEGACY_LABEL}.plist",
                "sha256": sha256_file(self.legacy_plist),
                "weekday": 3,
                "hour": 7,
                "minute": 30,
                "installed_at": "2026-10-01T00:00:00+00:00",
            }
        write(self.legacy_state / "manifest.json", json.dumps(manifest, indent=2) + "\n")

    def migrate(self, apply: bool, platform: str = "darwin") -> legacy_install.Migration:
        context = legacy_install.ScheduleContext(platform=platform, uid=UID, runner=self.launchctl, python=PYTHON)
        with redirect_stdout(io.StringIO()):
            return legacy_install.migrate(Framework(self.framework), apply, context)

    def manifest(self) -> dict:
        return json.loads((self.state / "manifest.json").read_text())

    def guard_command(self, data: dict, *path: str) -> str:
        node = data
        for key in path:
            node = node[key]
        return node[0]["hooks"][0]["command"]


class MigrationTests(LegacyDeviceTestCase):
    def test_fresh_device_has_nothing_to_migrate(self) -> None:
        before = self.snapshot(self.home)
        migration = self.migrate(apply=True)
        self.assertEqual((migration.steps, migration.warnings), ([], []))
        self.assertEqual(before, self.snapshot(self.home))
        self.assertEqual(self.launchctl.calls, [])

    def test_dry_run_describes_every_step_and_writes_nothing(self) -> None:
        self.make_legacy_device()
        before = self.snapshot(self.home)
        migration = self.migrate(apply=False)
        text = "\n".join(migration.steps)
        for needle in (
            "~/.local/state/agent-framework -> ~/.local/state/pakem",
            "~/.local/share/agent-framework -> ~/.local/share/pakem",
            "refresh   ~/.local/share/pakem/guard.py",
            "rewrite   ~/.claude/settings.json",
            "key 'agent-framework-guard' -> 'pakem-guard'",
            "~/.codex/rules/agent-framework.rules -> ~/.codex/rules/pakem.rules",
            f"launchd job {LEGACY_LABEL} -> {LABEL}",
            f"launchctl bootout gui/{UID}/{LEGACY_LABEL}",
        ):
            self.assertIn(needle, text)
        self.assertEqual(migration.warnings, [])
        self.assertEqual(before, self.snapshot(self.home))
        self.assertEqual(self.launchctl.calls, [])

    def test_apply_moves_state_and_renames_recorded_entries(self) -> None:
        self.make_legacy_device()
        migration = self.migrate(apply=True)
        self.assertEqual(migration.warnings, [])

        self.assertFalse(self.legacy_state.exists())
        self.assertFalse(self.legacy_share.exists())
        self.assertEqual((self.state / "reports" / "latest.md").read_text(), "# report\n")
        manifest = self.manifest()
        self.assertEqual(manifest["backups"][0]["backup_path"], f"~/.local/state/pakem/{BACKUP_PATH}")
        self.assertTrue((self.state / BACKUP_PATH).is_file())

        guard = self.share / "guard.py"
        self.assertEqual(guard.read_text(), (self.framework / "enforcement" / "guard.py").read_text())
        self.assertEqual(manifest["enforcement"]["guard"]["path"], "~/.local/share/pakem/guard.py")
        self.assertEqual(manifest["enforcement"]["guard"]["sha256"], sha256_file(guard))

        settings = json.loads(self.claude_settings.read_text())
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["env"], {"EDITOR": "vim"})
        self.assertEqual(settings["hooks"]["PostToolUse"], USER_SETTINGS["hooks"]["PostToolUse"])
        self.assertIn("$HOME/.local/share/pakem/guard.py", self.guard_command(settings, "hooks", "PreToolUse"))
        hooks = json.loads(self.antigravity_hooks.read_text())
        self.assertEqual(list(hooks), ["pakem-guard"])
        self.assertIn("$HOME/.local/share/pakem/guard.py", self.guard_command(hooks, "pakem-guard", "PreToolUse"))
        self.assertEqual(
            [record["reason"] for record in manifest["backups"]], ["enforce-claude", "rename-claude", "rename-antigravity"]
        )

        self.assertFalse(self.legacy_rules.exists())
        self.assertEqual(self.rules.read_text(), FORMER_RULES)
        self.assertEqual(manifest["enforcement"]["codex"]["target"], "~/.codex/rules/pakem.rules")

        self.assertEqual(
            self.launchctl.calls,
            [["bootout", f"gui/{UID}/{LEGACY_LABEL}"], ["bootstrap", f"gui/{UID}", str(self.plist)]],
        )
        self.assertFalse(self.legacy_plist.exists())
        job = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(job["Label"], LABEL)
        self.assertEqual(job["ProgramArguments"], [PYTHON, str(self.framework / "tools" / "pakem"), "report"])
        self.assertEqual(job["StartCalendarInterval"], {"Weekday": 3, "Hour": 7, "Minute": 30})
        self.assertEqual(job["StandardOutPath"], str(self.state / "reports" / "launchd.log"))
        self.assertEqual(list(manifest["schedule"]), [LABEL])
        self.assertEqual(manifest["schedule"][LABEL]["sha256"], sha256_file(self.plist))

    def test_migrated_host_settings_match_the_current_fragments(self) -> None:
        self.make_legacy_device(with_schedule=False)
        self.migrate(apply=True)
        for host in ("claude", "antigravity"):
            with self.subTest(host=host):
                output = io.StringIO()
                with redirect_stdout(output):
                    enforce.install_json_host(Framework(self.framework), host, apply=False)
                self.assertIn("already contains the fragment", output.getvalue())
                self.assertNotIn("remove stale", output.getvalue())

    def test_second_run_changes_nothing(self) -> None:
        self.make_legacy_device()
        self.migrate(apply=True)
        self.launchctl.calls.clear()
        before = self.snapshot(self.home)
        migration = self.migrate(apply=True)
        self.assertEqual((migration.steps, migration.warnings), ([], []))
        self.assertEqual(before, self.snapshot(self.home))
        self.assertEqual(self.launchctl.calls, [])

    def test_edited_files_are_reported_and_left_alone(self) -> None:
        self.make_legacy_device()
        write(self.legacy_share / "guard.py", "# edited by the owner\n")
        write(self.legacy_rules, "# edited rules\n")
        write(self.legacy_plist, FORMER_PLIST.replace("former", "edited"))
        migration = self.migrate(apply=True)

        self.assertEqual(len(migration.warnings), 3, migration.warnings)
        self.assertEqual((self.share / "guard.py").read_text(), "# edited by the owner\n")
        self.assertEqual(self.legacy_rules.read_text(), "# edited rules\n")
        self.assertFalse(self.rules.exists())
        self.assertIn("edited", self.legacy_plist.read_text())
        self.assertFalse(self.plist.exists())
        self.assertEqual(self.launchctl.calls, [])
        self.assertIn(LEGACY_LABEL, self.manifest()["schedule"])

    def test_files_the_manifest_does_not_record_are_not_touched(self) -> None:
        write(self.legacy_state / "manifest.json", json.dumps({"schema": 1, "entries": []}) + "\n")
        write(self.legacy_plist, FORMER_PLIST)
        write(self.legacy_rules, FORMER_RULES)
        own_settings = {"hooks": {"PreToolUse": [{"command": 'python3 "$HOME/.local/share/agent-framework/guard.py"'}]}}
        write(self.claude_settings, json.dumps(own_settings))
        before_settings = self.claude_settings.read_text()

        migration = self.migrate(apply=True)
        self.assertEqual(migration.warnings, [])
        self.assertTrue((self.state / "manifest.json").is_file())
        self.assertEqual(self.legacy_plist.read_text(), FORMER_PLIST)
        self.assertEqual(self.legacy_rules.read_text(), FORMER_RULES)
        self.assertEqual(self.claude_settings.read_text(), before_settings)
        self.assertEqual(self.launchctl.calls, [])

    def test_both_state_directories_existing_is_reported_not_merged(self) -> None:
        self.make_legacy_device(with_schedule=False)
        write(self.state / "reports" / "latest.md", "# newer report\n")
        migration = self.migrate(apply=True)
        self.assertTrue(any("both exist" in warning for warning in migration.warnings), migration.warnings)
        self.assertTrue((self.legacy_state / "manifest.json").is_file())
        self.assertEqual((self.state / "reports" / "latest.md").read_text(), "# newer report\n")

    def test_failed_bootstrap_is_reported_without_a_half_written_job(self) -> None:
        self.make_legacy_device()
        self.launchctl.returncodes = {"bootstrap": 5}
        migration = self.migrate(apply=True)
        self.assertTrue(any("pakem schedule install --apply" in warning for warning in migration.warnings))
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.legacy_plist.exists())
        self.assertEqual(self.manifest()["schedule"], {})

    def test_report_config_at_the_former_path_is_only_reported(self) -> None:
        config = self.home / ".config" / "agent-framework" / "report.toml"
        write(config, "repos_dirs = []\n")
        migration = self.migrate(apply=True)
        self.assertEqual(migration.steps, [])
        self.assertIn("move it to ~/.config/pakem/report.toml", migration.warnings[0])
        self.assertEqual(config.read_text(), "repos_dirs = []\n")


class MigrationCommandTests(LegacyDeviceTestCase):
    def test_doctor_reports_the_former_state(self) -> None:
        self.make_legacy_device(with_schedule=False)
        completed = self.pakem("doctor")
        self.assertIn("installed state is still under the former name agent-framework", completed.stdout)

    def test_refused_install_migrates_nothing(self) -> None:
        self.make_legacy_device(with_schedule=False)
        before = self.snapshot(self.home)
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertEqual(before, self.snapshot(self.home))

    def test_install_apply_migrates_then_installs_and_reruns_quietly(self) -> None:
        self.make_legacy_device(with_schedule=False)
        dry_run = self.pakem("install", "--allow-dirty")
        self.assert_ok(dry_run)
        self.assertIn("Migration from the former name agent-framework (dry-run):", dry_run.stdout)
        self.assertTrue(self.legacy_state.is_dir())

        applied = self.pakem("install", "--allow-dirty", "--apply")
        self.assert_ok(applied)
        self.assertIn("Migration from the former name agent-framework (apply):", applied.stdout)
        self.assertFalse(self.legacy_state.exists())
        self.assertFalse(self.legacy_share.exists())
        manifest = self.manifest()
        self.assertTrue(manifest["entries"])
        self.assertIn("pakem", manifest["enforcement"]["guard"]["path"])
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").read_text().startswith("<!-- pakem kernel v"))

        again = self.pakem("install", "--allow-dirty", "--apply")
        self.assert_ok(again)
        self.assertNotIn("Migration from", again.stdout)
        self.assertNotIn("installed state is still under the former name", self.pakem("doctor").stdout)

    def test_enforce_apply_migrates_first(self) -> None:
        self.make_legacy_device(with_schedule=False)
        completed = self.pakem("enforce", "install", "--host", "antigravity", "--apply")
        self.assert_ok(completed)
        self.assertIn("Migration from the former name agent-framework (apply):", completed.stdout)
        self.assertIn("already contains the fragment", completed.stdout)
        self.assertFalse(self.legacy_state.exists())


if __name__ == "__main__":
    unittest.main()
