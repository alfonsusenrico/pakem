"""af enforce install/uninstall for Claude Code, Codex, and Antigravity."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import REPO_ROOT, IsolatedTestCase, isolated_env, write

EXISTING_SETTINGS = {
    "model": "opus",
    "permissions": {
        "allow": ["Bash(npm test)"],
        "deny": ["Read(//**/.env)", "Bash(rm -rf *)"],
    },
    "hooks": {
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}],
    },
    "statusLine": {"type": "command", "command": "status.sh"},
}


class EnforceTests(IsolatedTestCase):
    def build_framework(self, root, **target_options):
        root = super().build_framework(root, **target_options)
        shutil.copytree(REPO_ROOT / "enforcement", root / "enforcement", dirs_exist_ok=True)
        return root

    def settings_path(self):
        return self.home / ".claude" / "settings.json"

    def manifest(self) -> dict:
        return json.loads((self.home / ".local" / "state" / "agent-framework" / "manifest.json").read_text())

    def test_claude_dry_run_prints_diff_and_writes_nothing(self) -> None:
        write(self.settings_path(), json.dumps(EXISTING_SETTINGS, indent=2) + "\n")
        before = self.snapshot(self.home)
        completed = self.af("enforce", "install", "--host", "claude")
        self.assert_ok(completed)
        self.assertIn('+      "Read(~/.ssh/**)",', completed.stdout)
        self.assertEqual(before, self.snapshot(self.home))

    def test_claude_merge_preserves_keys_and_uninstall_removes_only_added(self) -> None:
        write(self.settings_path(), json.dumps(EXISTING_SETTINGS, indent=2) + "\n")
        self.assert_ok(self.af("enforce", "install", "--host", "claude", "--apply"))

        merged = json.loads(self.settings_path().read_text())
        self.assertEqual(list(merged)[:4], ["model", "permissions", "hooks", "statusLine"])
        self.assertEqual(merged["model"], "opus")
        self.assertEqual(merged["statusLine"], EXISTING_SETTINGS["statusLine"])
        self.assertEqual(merged["permissions"]["allow"], ["Bash(npm test)"])
        deny = merged["permissions"]["deny"]
        self.assertEqual(deny.count("Read(//**/.env)"), 1)
        self.assertIn("Bash(rm -rf *)", deny)
        self.assertIn("Read(~/.ssh/**)", deny)
        self.assertIn("PostToolUse", merged["hooks"])
        self.assertIn("guard.py", merged["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertEqual(merged["attribution"], {"commit": "", "pr": ""})
        guard = self.home / ".local" / "share" / "agent-framework" / "guard.py"
        self.assertTrue(guard.is_file())
        self.assertTrue(self.manifest()["backups"])

        again = self.af("enforce", "install", "--host", "claude", "--apply")
        self.assert_ok(again)
        self.assertIn("already contains the fragment", again.stdout)

        merged["permissions"]["deny"].append("Read(./private/**)")
        self.settings_path().write_text(json.dumps(merged, indent=2) + "\n")

        self.assert_ok(self.af("enforce", "uninstall", "--host", "claude", "--apply"))
        restored = json.loads(self.settings_path().read_text())
        expected = json.loads(json.dumps(EXISTING_SETTINGS))
        expected["permissions"]["deny"].append("Read(./private/**)")
        self.assertEqual(restored, expected)
        self.assertFalse(guard.exists())
        self.assertNotIn("claude", self.manifest()["enforcement"])

    def test_reinstall_removes_entries_dropped_from_the_fragment(self) -> None:
        write(self.settings_path(), json.dumps(EXISTING_SETTINGS, indent=2) + "\n")
        fragment_path = self.framework / "enforcement" / "claude" / "settings.fragment.json"
        fragment = json.loads(fragment_path.read_text())
        fragment["permissions"]["deny"].append("Bash(git push origin main)")
        fragment_path.write_text(json.dumps(fragment, indent=2) + "\n")
        self.assert_ok(self.af("enforce", "install", "--host", "claude", "--apply"))
        self.assertIn("Bash(git push origin main)", json.loads(self.settings_path().read_text())["permissions"]["deny"])

        fragment["permissions"]["deny"].remove("Bash(git push origin main)")
        fragment_path.write_text(json.dumps(fragment, indent=2) + "\n")
        reinstalled = self.af("enforce", "install", "--host", "claude", "--apply")
        self.assert_ok(reinstalled)
        self.assertIn('remove stale: permissions.deny "Bash(git push origin main)"', reinstalled.stdout)
        deny = json.loads(self.settings_path().read_text())["permissions"]["deny"]
        self.assertNotIn("Bash(git push origin main)", deny)
        self.assertIn("Bash(rm -rf *)", deny)
        self.assertIn("Read(//**/.env)", deny)

        self.assert_ok(self.af("enforce", "uninstall", "--host", "claude", "--apply"))
        self.assertEqual(json.loads(self.settings_path().read_text()), EXISTING_SETTINGS)

    def test_claude_settings_created_and_removed_when_absent(self) -> None:
        self.assert_ok(self.af("enforce", "install", "--host", "claude", "--apply"))
        self.assertTrue(self.settings_path().is_file())
        self.assert_ok(self.af("enforce", "uninstall", "--host", "claude", "--apply"))
        self.assertFalse(self.settings_path().exists())

    def test_claude_invalid_settings_refused(self) -> None:
        write(self.settings_path(), "{ not json")
        completed = self.af("enforce", "install", "--host", "claude", "--apply")
        self.assert_fails(completed, code=2)
        self.assertEqual(self.settings_path().read_text(), "{ not json")

    def test_antigravity_named_hook_merge(self) -> None:
        hooks_path = self.home / ".gemini" / "config" / "hooks.json"
        other = {
            "lint-checker": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "lint"}]}]}
        }
        write(hooks_path, json.dumps(other, indent=2) + "\n")
        self.assert_ok(self.af("enforce", "install", "--host", "antigravity", "--apply"))
        merged = json.loads(hooks_path.read_text())
        self.assertEqual(merged["lint-checker"], other["lint-checker"])
        guard_hook = merged["agent-framework-guard"]["PreToolUse"][0]
        self.assertEqual(guard_hook["matcher"], "run_command|view_file|.*read.*")
        self.assertIn("--host antigravity", guard_hook["hooks"][0]["command"])

        self.assert_ok(self.af("enforce", "install", "--host", "claude", "--apply"))
        self.assert_ok(self.af("enforce", "uninstall", "--host", "antigravity", "--apply"))
        self.assertEqual(json.loads(hooks_path.read_text()), other)
        guard = self.home / ".local" / "share" / "agent-framework" / "guard.py"
        self.assertTrue(guard.exists(), "guard must stay while claude still uses it")

    def test_codex_rules_install_and_uninstall(self) -> None:
        rules = self.home / ".codex" / "rules" / "agent-framework.rules"
        other_rules = write(self.home / ".codex" / "rules" / "default.rules", "# owner rules\n")
        self.assert_ok(self.af("enforce", "install", "--host", "codex", "--apply"))
        self.assertEqual(
            rules.read_text(),
            (self.framework / "enforcement" / "codex" / "agent-framework.rules").read_text(),
        )
        self.assert_ok(self.af("enforce", "uninstall", "--host", "codex", "--apply"))
        self.assertFalse(rules.exists())
        self.assertEqual(other_rules.read_text(), "# owner rules\n")

    def test_codex_refuses_unmanaged_rules_file(self) -> None:
        write(self.home / ".codex" / "rules" / "agent-framework.rules", "# someone else's\n")
        self.assert_fails(self.af("enforce", "install", "--host", "codex", "--apply"))


CODEX_RULES = REPO_ROOT / "enforcement" / "codex" / "agent-framework.rules"
DESTRUCTIVE_REFSPEC_FORMS = ("+{}", ":{}", "+HEAD:{}", ":refs/heads/{}")
PLAIN_REFSPEC_FORMS = ("{}", "HEAD:{}", "refs/heads/{}", "HEAD:refs/heads/{}")


class CodexRulesContentTests(unittest.TestCase):
    def test_every_destructive_refspec_form_is_listed_for_each_protected_branch(self) -> None:
        text = CODEX_RULES.read_text()
        for branch in ("main", "master", "development"):
            for form in DESTRUCTIVE_REFSPEC_FORMS:
                with self.subTest(refspec=form.format(branch)):
                    self.assertIn(f'"{form.format(branch)}",', text)


@unittest.skipIf(shutil.which("codex") is None, "codex CLI not installed")
class CodexRulesSyntaxTests(unittest.TestCase):
    def decision(self, *command: str):
        with tempfile.TemporaryDirectory(prefix="af-codex-home-") as home:
            completed = subprocess.run(
                ["codex", "execpolicy", "check", "--rules", str(CODEX_RULES), *command],
                capture_output=True,
                text=True,
                env=isolated_env(Path(home)),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout).get("decision")

    def test_decisions(self) -> None:
        self.assertEqual(self.decision("git", "push", "--force", "origin", "feat/x"), "forbidden")
        self.assertEqual(self.decision("git", "push", "--no-verify", "origin", "main"), "forbidden")
        self.assertEqual(self.decision("git", "commit", "--no-verify", "-m", "x"), "forbidden")
        self.assertEqual(self.decision("printenv"), "forbidden")
        self.assertIsNone(self.decision("git", "push", "origin", "feat/x"))
        self.assertIsNone(self.decision("git", "commit", "-m", "feat: x"))

    def test_plain_pushes_to_protected_branches_are_left_to_the_hook(self) -> None:
        for branch in ("main", "master", "development"):
            for form in PLAIN_REFSPEC_FORMS:
                refspec = form.format(branch)
                with self.subTest(refspec=refspec):
                    self.assertIsNone(self.decision("git", "push", "origin", refspec))

    def test_force_and_delete_of_protected_branches_are_forbidden(self) -> None:
        for branch in ("main", "master", "development"):
            for form in DESTRUCTIVE_REFSPEC_FORMS:
                refspec = form.format(branch)
                with self.subTest(refspec=refspec):
                    self.assertEqual(self.decision("git", "push", "origin", refspec), "forbidden")
        self.assertIsNone(self.decision("git", "push", "origin", "development-docs"))


if __name__ == "__main__":
    unittest.main()
