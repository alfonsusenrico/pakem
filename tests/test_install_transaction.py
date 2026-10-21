"""pakem install --apply rolls back every write of the run when one write fails."""

from __future__ import annotations

import os
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path

from support import IsolatedTestCase, write

INSTALL = ("install", "--allow-dirty", "--apply")
SKILL_DIRS = (".claude/skills", ".agents/skills", ".gemini/config/skills")


@contextmanager
def read_only(directory: Path):
    """Make directory unwritable for the duration, so creating or renaming entries in it fails."""
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o555)
    try:
        yield directory
    finally:
        os.chmod(directory, 0o755)


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory permissions")
class InstallTransactionTests(IsolatedTestCase):
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "pakem"

    def manifest_bytes(self) -> bytes:
        return (self.state_dir() / "manifest.json").read_bytes()

    def change_framework_content(self) -> None:
        kernel = self.framework / "kernel" / "KERNEL.md"
        write(kernel, kernel.read_text() + "New line.\n")
        skill = self.framework / "skills" / "demo-skill" / "SKILL.md"
        write(skill, skill.read_text() + "More guidance.\n")

    def assert_no_rollback_leftovers(self) -> None:
        leftovers = [path for path in self.home.rglob("*") if ".pakem-rollback" in path.name or ".pakem-" in path.name]
        self.assertEqual(leftovers, [])

    def test_failed_fresh_install_leaves_home_as_it_was(self) -> None:
        with read_only(self.home / ".gemini"):
            before = self.snapshot(self.home)
            completed = self.pakem(*INSTALL)
            after = self.snapshot(self.home)
        self.assert_fails(completed, code=2)
        self.assertIn("install failed while writing ~/.gemini/GEMINI.md (PermissionError)", completed.stderr)
        self.assertIn("manifest unchanged", completed.stderr)
        self.assertIn("removed ~/.claude/CLAUDE.md (created in this run)", completed.stdout)
        self.assertIn("removed directory ~/.claude (created in this run)", completed.stdout)
        self.assertIn("removed ~/.codex/AGENTS.md (created in this run)", completed.stdout)
        self.assertEqual(before, after)
        self.assertFalse((self.home / ".local").exists())

    def test_failed_update_restores_previous_files_and_manifest(self) -> None:
        self.assert_ok(self.pakem(*INSTALL))
        claude = self.home / ".claude" / "CLAUDE.md"
        codex = self.home / ".codex" / "AGENTS.md"
        old_claude, old_codex = claude.read_text(), codex.read_text()
        old_manifest = self.manifest_bytes()
        self.change_framework_content()

        with read_only(self.home / ".gemini"):
            completed = self.pakem(*INSTALL)
        self.assert_fails(completed, code=2)
        self.assertIn("restored ~/.claude/CLAUDE.md", completed.stdout)
        self.assertIn("restored ~/.codex/AGENTS.md", completed.stdout)
        self.assertEqual(claude.read_text(), old_claude)
        self.assertEqual(codex.read_text(), old_codex)
        self.assertEqual(self.manifest_bytes(), old_manifest)
        self.assert_no_rollback_leftovers()

        self.assert_ok(self.pakem(*INSTALL))
        self.assertIn("New line.", claude.read_text())

    def test_failed_manifest_save_restores_written_and_pruned_targets(self) -> None:
        self.assert_ok(self.pakem(*INSTALL))
        old_manifest = self.manifest_bytes()
        old_claude = (self.home / ".claude" / "CLAUDE.md").read_text()
        old_skills = {skills_dir: self.snapshot(self.home / skills_dir) for skills_dir in SKILL_DIRS}
        self.change_framework_content()
        shutil.rmtree(self.framework / "skills" / "project-bootstrap")

        with read_only(self.state_dir()):
            completed = self.pakem(*INSTALL)
        self.assert_fails(completed, code=2)
        self.assertIn("install failed while writing the manifest", completed.stderr)
        self.assertIn("restored ~/.claude/skills/project-bootstrap", completed.stdout)
        self.assertEqual(self.manifest_bytes(), old_manifest)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), old_claude)
        for skills_dir in SKILL_DIRS:
            self.assertEqual(self.snapshot(self.home / skills_dir), old_skills[skills_dir])
        self.assert_no_rollback_leftovers()

    def test_failed_install_restores_backed_up_unmanaged_file(self) -> None:
        own_rules = write(self.home / ".claude" / "CLAUDE.md", "my own rules\n")
        with read_only(self.home / ".gemini"):
            completed = self.pakem(*INSTALL)
        self.assert_fails(completed, code=2)
        self.assertIn("restored ~/.claude/CLAUDE.md", completed.stdout)
        self.assertIn("Backups taken in this run remain in ~/.local/state/pakem/backups/", completed.stdout)
        self.assertEqual(own_rules.read_text(), "my own rules\n")
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.state_dir() / "manifest.json").exists())
        self.assert_no_rollback_leftovers()

    def test_failed_skill_write_rolls_back_earlier_skills(self) -> None:
        with read_only(self.home / ".gemini" / "config" / "skills"):
            completed = self.pakem(*INSTALL)
        self.assert_fails(completed, code=2)
        self.assertIn("install failed while writing ~/.gemini/config/skills/", completed.stderr)
        self.assertIn("removed ~/.claude/skills/demo-skill (created in this run)", completed.stdout)
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".gemini" / "GEMINI.md").exists())
        self.assertEqual(list((self.home / ".gemini" / "config" / "skills").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
