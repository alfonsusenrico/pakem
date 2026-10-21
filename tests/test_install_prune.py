"""pakem install prune of skills whose source is gone, and pakem doctor orphan reporting."""

from __future__ import annotations

import json
import shutil
import unittest

from support import IsolatedTestCase, write

INSTALL = ("install", "--allow-dirty")
SKILL_DIRS = (".claude/skills", ".agents/skills", ".gemini/config/skills")
PROFILE_TOML = 'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1,<2.0"\ndigest = "DIGEST.md"\nskills_dir = "skills"\n'


def skill_text(name: str) -> str:
    return f"---\nname: {name}\ndescription: Profile skill {name}.\n---\nBody\n"


class PruneTests(IsolatedTestCase):
    def manifest(self) -> dict:
        path = self.home / ".local" / "state" / "agent-framework" / "manifest.json"
        return json.loads(path.read_text())

    def manifest_paths(self) -> set[str]:
        return {entry["path"] for entry in self.manifest()["entries"]}

    def plan_lines(self, stdout: str, action: str) -> list[str]:
        return [line for line in stdout.splitlines() if line.strip().startswith(action + " ")]

    def make_profile(self, *skills: str):
        profile = self.base / "profile"
        write(profile / "profile.toml", PROFILE_TOML)
        write(profile / "DIGEST.md", "Work rules.\n")
        for name in skills:
            write(profile / "skills" / name / "SKILL.md", skill_text(name))
        return profile

    def remove_framework_skill(self) -> None:
        shutil.rmtree(self.framework / "skills" / "demo-skill")

    def test_removed_framework_skill_is_pruned(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.remove_framework_skill()

        dry_run = self.pakem(*INSTALL)
        self.assert_ok(dry_run)
        removals = self.plan_lines(dry_run.stdout, "remove-managed")
        self.assertEqual(len(removals), 3, dry_run.stdout)
        self.assertIn("source no longer exists", removals[0])
        self.assertTrue((self.home / ".claude" / "skills" / "demo-skill").is_dir())

        applied = self.pakem(*INSTALL, "--apply")
        self.assert_ok(applied)
        for skills_dir in SKILL_DIRS:
            self.assertFalse((self.home / skills_dir / "demo-skill").exists())
            self.assertTrue((self.home / skills_dir / "project-bootstrap").is_dir())
            self.assertNotIn(f"~/{skills_dir}/demo-skill", self.manifest_paths())
            self.assertEqual(list((self.home / skills_dir).glob(".*")), [])

        again = self.pakem(*INSTALL)
        self.assert_ok(again)
        self.assertEqual(self.plan_lines(again.stdout, "remove-managed"), [])

    def test_edited_orphan_blocks_unless_forced(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        edited = self.home / ".claude" / "skills" / "demo-skill" / "SKILL.md"
        edited.write_text("my local notes\n")
        self.remove_framework_skill()

        blocked = self.pakem(*INSTALL, "--apply")
        self.assert_fails(blocked)
        self.assertIn("BLOCKED-locally-edited", blocked.stdout)
        self.assertIn("installed copy was edited since install", blocked.stdout)
        for skills_dir in SKILL_DIRS:
            self.assertTrue((self.home / skills_dir / "demo-skill").is_dir())

        forced = self.pakem(*INSTALL, "--apply", "--force")
        self.assert_ok(forced)
        self.assertIn("backup-and-remove-edited", forced.stdout)
        self.assertFalse(edited.parent.exists())
        backups = [record for record in self.manifest()["backups"] if record["original"].endswith("demo-skill")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["reason"], "locally-edited")
        backup_dir = self.home / backups[0]["backup_path"].removeprefix("~/")
        self.assertEqual((backup_dir / "SKILL.md").read_text(), "my local notes\n")

    def test_already_absent_orphan_drops_manifest_entry(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.remove_framework_skill()
        shutil.rmtree(self.home / ".claude" / "skills" / "demo-skill")
        completed = self.pakem(*INSTALL, "--apply")
        self.assert_ok(completed)
        self.assertEqual(len(self.plan_lines(completed.stdout, "already-absent")), 1, completed.stdout)
        self.assertEqual(len(self.plan_lines(completed.stdout, "remove-managed")), 2, completed.stdout)
        self.assertFalse(any(path.endswith("/demo-skill") for path in self.manifest_paths()))

    def test_profile_skills_are_kept_when_profile_not_given(self) -> None:
        profile = self.make_profile("work-skill")
        self.assert_ok(self.pakem(*INSTALL, "--profile", str(profile), "--apply"))

        completed = self.pakem(*INSTALL, "--apply")
        self.assert_ok(completed)
        kept = self.plan_lines(completed.stdout, "kept")
        self.assertEqual(len(kept), 3, completed.stdout)
        self.assertIn("profile not given", kept[0])
        self.assertIn("3 kept", completed.stdout)
        for skills_dir in SKILL_DIRS:
            self.assertTrue((self.home / skills_dir / "work-skill").is_dir())
            self.assertIn(f"~/{skills_dir}/work-skill", self.manifest_paths())

    def test_removed_profile_skill_is_pruned_when_profile_given(self) -> None:
        profile = self.make_profile("work-skill", "old-skill")
        self.assert_ok(self.pakem(*INSTALL, "--profile", str(profile), "--apply"))
        shutil.rmtree(profile / "skills" / "old-skill")

        completed = self.pakem(*INSTALL, "--profile", str(profile), "--apply")
        self.assert_ok(completed)
        removals = self.plan_lines(completed.stdout, "remove-managed")
        self.assertEqual(len(removals), 3, completed.stdout)
        self.assertIn("profile:work/old-skill", removals[0])
        for skills_dir in SKILL_DIRS:
            self.assertFalse((self.home / skills_dir / "old-skill").exists())
            self.assertTrue((self.home / skills_dir / "work-skill").is_dir())

    def test_prune_is_limited_to_selected_hosts(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        self.remove_framework_skill()
        self.assert_ok(self.pakem(*INSTALL, "--hosts", "claude", "--apply"))
        self.assertFalse((self.home / ".claude" / "skills" / "demo-skill").exists())
        self.assertTrue((self.home / ".agents" / "skills" / "demo-skill").is_dir())
        self.assertIn("~/.agents/skills/demo-skill", self.manifest_paths())


class DoctorOrphanTests(IsolatedTestCase):
    def test_reports_orphaned_framework_skill(self) -> None:
        self.assert_ok(self.pakem(*INSTALL, "--apply"))
        shutil.rmtree(self.framework / "skills" / "demo-skill")
        completed = self.pakem("doctor")
        self.assertIn("WARNING: orphaned: ~/.claude/skills/demo-skill was installed from skills/demo-skill", completed.stdout)
        self.assertIn("orphaned: ~/.agents/skills/demo-skill", completed.stdout)

    def test_profile_orphans_reported_only_for_given_profiles(self) -> None:
        profile = self.base / "profile"
        write(profile / "profile.toml", PROFILE_TOML)
        write(profile / "DIGEST.md", "Work rules.\n")
        write(profile / "skills" / "work-skill" / "SKILL.md", skill_text("work-skill"))
        write(profile / "skills" / "old-skill" / "SKILL.md", skill_text("old-skill"))
        self.assert_ok(self.pakem(*INSTALL, "--profile", str(profile), "--apply"))
        shutil.rmtree(profile / "skills" / "old-skill")

        without_profile = self.pakem("doctor")
        self.assertNotIn("orphaned", without_profile.stdout)
        with_profile = self.pakem("doctor", "--profile", str(profile))
        self.assertIn("orphaned: ~/.claude/skills/old-skill was installed from profile:work/old-skill", with_profile.stdout)
        self.assertNotIn("work-skill was installed", with_profile.stdout)


if __name__ == "__main__":
    unittest.main()
