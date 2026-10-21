"""pakem install / uninstall / doctor against a temporary HOME."""

from __future__ import annotations

import json
import os
import unittest

from support import IsolatedTestCase, activate_hooks, init_repo, git, targets_toml, write

INSTALL = ("install", "--allow-dirty")


class InstallTests(IsolatedTestCase):
    def manifest(self) -> dict:
        path = self.home / ".local" / "state" / "agent-framework" / "manifest.json"
        return json.loads(path.read_text())

    def test_dry_run_writes_nothing(self) -> None:
        before = self.snapshot(self.home)
        completed = self.pakem(*INSTALL)
        self.assert_ok(completed)
        self.assertIn("create", completed.stdout)
        self.assertIn("Dry run", completed.stdout)
        self.assertEqual(before, self.snapshot(self.home))

    def test_apply_creates_files_and_manifest(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        claude = (self.home / ".claude" / "CLAUDE.md").read_text()
        self.assertTrue(claude.startswith("<!-- agent-framework kernel v0.2.0 sha256="))
        self.assertIn("Always verify before claiming success.", claude)
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").is_symlink())
        for skills_dir in (".claude/skills", ".agents/skills", ".gemini/config/skills"):
            self.assertTrue((self.home / skills_dir / "demo-skill" / "references" / "guide.md").is_file())
        manifest = self.manifest()
        self.assertEqual(manifest["framework_version"], "0.2.0")
        kinds = sorted(entry["kind"] for entry in manifest["entries"])
        # Two fixture skills (demo-skill, project-bootstrap) in three host skill directories.
        self.assertEqual(kinds, ["instructions"] * 3 + ["skill"] * 6)
        paths = {entry["path"] for entry in manifest["entries"]}
        self.assertIn("~/.codex/AGENTS.md", paths)

    def test_second_apply_is_unchanged(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        completed = self.pakem(*INSTALL, "--apply")
        self.assert_ok(completed)
        plan_lines = [line for line in completed.stdout.splitlines() if line.startswith("  ")]
        self.assertTrue(plan_lines)
        self.assertTrue(all(line.strip().startswith("unchanged") for line in plan_lines), completed.stdout)

    def test_ds_store_is_not_copied(self) -> None:
        write(self.framework / "skills" / "demo-skill" / ".DS_Store", "junk")
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.assertFalse((self.home / ".claude" / "skills" / "demo-skill" / ".DS_Store").exists())

    def test_symlink_is_backed_up_and_restored_by_uninstall(self) -> None:
        real_target = write(self.base / "elsewhere" / "AGENTS.md", "owner rules\n")
        link = self.home / ".codex" / "AGENTS.md"
        link.parent.mkdir(parents=True)
        os.symlink(real_target, link)

        dry_run = self.pakem(*INSTALL)
        self.assert_ok(dry_run)
        self.assertIn("backup-and-replace-symlink", dry_run.stdout)

        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.assertFalse(link.is_symlink())
        self.assertEqual(real_target.read_text(), "owner rules\n")
        backups = self.manifest()["backups"]
        self.assertEqual(backups[0]["link_target"], str(real_target))
        self.assertNotIn("backup_path", backups[0])
        index_files = list(
            (self.home / ".local" / "state" / "agent-framework" / "backups").rglob("backup-index.json")
        )
        self.assertEqual(len(index_files), 1)
        self.assertIn(str(real_target), index_files[0].read_text())

        self.assert_ok(self.pakem("uninstall", "--apply"))
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(real_target))
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "demo-skill").exists())

    def test_unmanaged_file_is_backed_up_and_restored(self) -> None:
        original = write(self.home / ".claude" / "CLAUDE.md", "my own rules\n")
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        backup = self.manifest()["backups"][0]
        self.assertEqual(backup["original"], "~/.claude/CLAUDE.md")
        backup_file = self.home / backup["backup_path"].removeprefix("~/")
        self.assertEqual(backup_file.read_text(), "my own rules\n")
        self.assert_ok(self.pakem("uninstall", "--apply"))
        self.assertEqual(original.read_text(), "my own rules\n")

    def test_locally_edited_managed_file_blocks_install(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        claude = self.home / ".claude" / "CLAUDE.md"
        claude.write_text(claude.read_text() + "local edit\n")
        write(
            self.framework / "kernel" / "KERNEL.md",
            (self.framework / "kernel" / "KERNEL.md").read_text() + "New line.\n",
        )

        completed = self.pakem(*INSTALL, "--apply")
        self.assert_fails(completed)
        self.assertIn("BLOCKED-locally-edited", completed.stdout)
        self.assertIn("local edit", claude.read_text())
        self.assertNotIn("New line.", (self.home / ".codex" / "AGENTS.md").read_text())

        forced = self.pakem(*INSTALL, "--apply", "--force")
        self.assert_ok(forced)
        self.assertNotIn("local edit", claude.read_text())
        reasons = [record["reason"] for record in self.manifest()["backups"]]
        self.assertIn("locally-edited", reasons)

    def test_size_limit_aborts_before_writing(self) -> None:
        write(self.framework / "hosts" / "targets.toml", targets_toml(claude_max_chars=50))
        completed = self.pakem(*INSTALL, "--apply")
        self.assert_fails(completed)
        self.assertIn("allows 50", completed.stdout)
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_skill_lock_collision_blocks(self) -> None:
        write(
            self.home / ".agents" / ".skill-lock.json",
            json.dumps({"skills": {"demo-skill": {"source": "x"}}}),
        )
        completed = self.pakem(*INSTALL, "--apply")
        self.assert_fails(completed)
        self.assertIn("BLOCKED-skill-lock", completed.stdout)
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())

    def test_unparsable_skill_lock_warns(self) -> None:
        write(self.home / ".agents" / ".skill-lock.json", "{not json")
        completed = self.pakem(*INSTALL)
        self.assert_ok(completed)
        self.assertIn("could not parse", completed.stdout)

    def test_unmanaged_skill_directory_blocks(self) -> None:
        write(self.home / ".claude" / "skills" / "demo-skill" / "SKILL.md", "someone else's skill\n")
        completed = self.pakem(*INSTALL)
        self.assert_fails(completed)
        self.assertIn("BLOCKED-unmanaged-skill", completed.stdout)

    def test_must_not_exist_warns_without_deleting(self) -> None:
        competing = write(self.home / ".gemini" / "AGENTS.md", "old\n")
        completed = self.pakem(*INSTALL, "--apply")
        self.assert_ok(completed)
        self.assertIn("WARNING", completed.stdout)
        self.assertIn("~/.gemini/AGENTS.md", completed.stdout)
        self.assertTrue(competing.exists())

    def test_dirty_framework_refuses_apply(self) -> None:
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        write(self.framework / "untracked.txt", "dirty\n")
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("uncommitted", refused.stderr)
        dry_run = self.pakem("install")
        self.assert_ok(dry_run)
        self.assertIn("uncommitted", dry_run.stdout)

    def test_profile_skills_are_installed(self) -> None:
        profile = self.base / "profile"
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1,<2.0"\ndigest = "DIGEST.md"\nskills_dir = "skills"\n',
        )
        write(profile / "DIGEST.md", "Work rules.\n")
        write(
            profile / "skills" / "work-skill" / "SKILL.md",
            "---\nname: work-skill\ndescription: Work.\n---\nBody\n",
        )
        self.assert_ok(self.pakem(*INSTALL, "--profile", str(profile), "--apply"))
        self.assertTrue((self.home / ".claude" / "skills" / "work-skill" / "SKILL.md").is_file())

    def test_uninstall_blocks_edited_file(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        claude = self.home / ".claude" / "CLAUDE.md"
        claude.write_text("edited\n")
        completed = self.pakem("uninstall", "--apply")
        self.assert_fails(completed)
        self.assertEqual(claude.read_text(), "edited\n")


class DoctorTests(IsolatedTestCase):
    def test_clean_install_passes(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.assert_ok(self.pakem("doctor"))

    def test_detects_edited_and_missing(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        (self.home / ".claude" / "CLAUDE.md").write_text("edited\n")
        (self.home / ".codex" / "AGENTS.md").unlink()
        completed = self.pakem("doctor")
        self.assert_fails(completed)
        self.assertIn("edited: ~/.claude/CLAUDE.md", completed.stdout)
        self.assertIn("missing: ~/.codex/AGENTS.md", completed.stdout)

    def test_detects_outdated_version(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        write(self.framework / "VERSION", "0.3.0\n")
        completed = self.pakem("doctor")
        self.assertIn("older than repo v0.3.0", completed.stdout)

    def test_detects_duplicate_global_instruction_files(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        write(self.home / ".gemini" / "AGENTS.md", "competing\n")
        completed = self.pakem("doctor")
        self.assert_fails(completed)
        self.assertIn("more than one global instruction file", completed.stdout)

    def test_detects_duplicate_skills_across_host_dirs(self) -> None:
        self.build_framework(self.framework, extra_codex_skills_dir=True)
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        completed = self.pakem("doctor")
        self.assert_fails(completed)
        self.assertIn("skill 'demo-skill' is present in more than one skills dir", completed.stdout)

    def test_repos_summary(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        projects = self.base / "projects"
        good = activate_hooks(init_repo(projects / "good", self.home), self.home)
        (good / "research").mkdir()
        init_repo(projects / "bare-notes", self.home)
        self.assert_ok(self.pakem("project", "sync", str(good), "--apply"))
        completed = self.pakem("doctor", "--repos", str(projects))
        self.assert_fails(completed)
        self.assertIn("ok        " + str(good), completed.stdout)
        self.assertIn("bare-notes: AGENTS.md not found", completed.stdout)


if __name__ == "__main__":
    unittest.main()
