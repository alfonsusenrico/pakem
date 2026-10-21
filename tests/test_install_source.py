"""pakem install source checks: merged main for the framework, clean main for git profiles."""

from __future__ import annotations

import unittest

from support import IsolatedTestCase, git, init_repo, write

PROFILE_TOML = 'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1,<2.0"\ndigest = "DIGEST.md"\nskills_dir = "skills"\n'


class FrameworkSourceTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_repo(self.framework, self.home)
        self.commit("chore: fixture")

    def commit(self, message: str) -> None:
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "--allow-empty", "-m", message, home=self.home)

    def add_origin(self) -> None:
        origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(origin), home=self.home)
        git(self.framework, "remote", "add", "origin", str(origin), home=self.home)
        git(self.framework, "push", "-q", "origin", "main", home=self.home)

    def installed(self) -> bool:
        return (self.home / ".claude" / "CLAUDE.md").exists()

    def test_clean_main_without_origin_installs(self) -> None:
        completed = self.pakem("install", "--apply")
        self.assert_ok(completed)
        self.assertIn("Source: framework main @ ", completed.stdout)
        self.assertIn("no origin remote; merge state not checked", completed.stdout)
        self.assertTrue(self.installed())

    def test_main_matching_origin_installs(self) -> None:
        self.add_origin()
        completed = self.pakem("install", "--apply")
        self.assert_ok(completed)
        self.assertIn("matches origin/main as of the last fetch; fetch to refresh", completed.stdout)
        self.assertNotIn("WARNING", completed.stdout)

    def test_feature_branch_refuses_apply_and_warns_in_dry_run(self) -> None:
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)

        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("on branch feat/topic, not main", refused.stderr)
        self.assertIn("pass --allow-unmerged to install anyway", refused.stderr)
        self.assertFalse(self.installed())
        self.assertFalse((self.home / ".local").exists())

        dry_run = self.pakem("install")
        self.assert_ok(dry_run)
        self.assertIn("WARNING: framework checkout is on branch feat/topic", dry_run.stdout)
        self.assertIn("--apply would refuse without --allow-unmerged", dry_run.stdout)

        allowed = self.pakem("install", "--apply", "--allow-unmerged")
        self.assert_ok(allowed)
        self.assertIn("(allowed by --allow-unmerged)", allowed.stdout)
        self.assertTrue(self.installed())

    def test_detached_head_refuses_apply(self) -> None:
        git(self.framework, "checkout", "-q", "--detach", home=self.home)
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("detached HEAD, not main", refused.stderr)

    def test_release_tag_in_origin_main_installs(self) -> None:
        self.add_origin()
        git(self.framework, "tag", "v2.0.0", home=self.home)
        self.commit("feat: later work")
        git(self.framework, "push", "-q", "origin", "main", home=self.home)
        git(self.framework, "fetch", "-q", "origin", home=self.home)
        git(self.framework, "checkout", "-q", "v2.0.0", home=self.home)
        completed = self.pakem("install", "--apply")
        self.assert_ok(completed)
        self.assertIn("release v2.0.0 @ ", completed.stdout)
        self.assertIn("part of origin/main", completed.stdout)
        self.assertNotIn("WARNING", completed.stdout)
        self.assertTrue(self.installed())

    def test_release_tag_outside_origin_main_refuses_apply(self) -> None:
        self.add_origin()
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)
        self.commit("feat: unpublished")
        git(self.framework, "tag", "v9.0.0", home=self.home)
        git(self.framework, "checkout", "-q", "v9.0.0", home=self.home)
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("release v9.0.0 is not part of origin/main", refused.stderr)
        self.assertFalse(self.installed())

    def test_local_commits_ahead_of_origin_refuse_apply(self) -> None:
        self.add_origin()
        self.commit("docs: unpushed")
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("differs from origin/main", refused.stderr)
        self.assertIn("1 ahead, 0 behind as of the last fetch; fetch to refresh", refused.stderr)
        self.assert_ok(self.pakem("install", "--apply", "--allow-unmerged"))

    def test_main_behind_origin_refuses_apply(self) -> None:
        self.commit("docs: second")
        self.add_origin()
        git(self.framework, "reset", "-q", "--hard", "HEAD~1", home=self.home)
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("0 ahead, 1 behind", refused.stderr)

    def test_unknown_origin_main_refuses_apply(self) -> None:
        git(self.framework, "remote", "add", "origin", str(self.base / "never-fetched.git"), home=self.home)
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("origin/main is not known locally", refused.stderr)
        self.assertIn("fetch to refresh", refused.stderr)
        self.assert_ok(self.pakem("install", "--apply", "--allow-unmerged"))

    def test_dirty_feature_branch_needs_both_waivers(self) -> None:
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)
        write(self.framework / "scratch.txt", "dirty\n")

        only_dirty = self.pakem("install", "--apply", "--allow-dirty")
        self.assert_fails(only_dirty, code=2)
        self.assertIn("--allow-unmerged", only_dirty.stderr)
        self.assertNotIn("uncommitted", only_dirty.stderr)

        only_unmerged = self.pakem("install", "--apply", "--allow-unmerged")
        self.assert_fails(only_unmerged, code=2)
        self.assertIn("uncommitted changes", only_unmerged.stderr)
        self.assertNotIn("feat/topic", only_unmerged.stderr)

        self.assert_ok(self.pakem("install", "--apply", "--allow-dirty", "--allow-unmerged"))
        self.assertTrue(self.installed())


class NonGitFrameworkTests(IsolatedTestCase):
    def test_non_git_root_needs_allow_dirty(self) -> None:
        refused = self.pakem("install", "--apply")
        self.assert_fails(refused, code=2)
        self.assertIn("is not a git work tree", refused.stderr)
        self.assertIn("pass --allow-dirty", refused.stderr)
        self.assert_ok(self.pakem("install", "--apply", "--allow-dirty"))


class ProfileSourceTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        self.profile = self.base / "profile"
        write(self.profile / "profile.toml", PROFILE_TOML)
        write(self.profile / "DIGEST.md", "Work rules.\n")
        write(
            self.profile / "skills" / "work-skill" / "SKILL.md",
            "---\nname: work-skill\ndescription: Work.\n---\nBody\n",
        )
        init_repo(self.profile, self.home)
        git(self.profile, "add", "-A", home=self.home)
        git(self.profile, "commit", "-q", "-m", "chore: profile", home=self.home)

    def install(self, *extra: str):
        return self.pakem("install", "--profile", str(self.profile), *extra)

    def make_profile_unmerged_and_dirty(self) -> None:
        git(self.profile, "checkout", "-q", "-b", "feat/profile", home=self.home)
        write(self.profile / "notes.txt", "dirty\n")

    def test_clean_profile_on_main_has_no_findings(self) -> None:
        completed = self.install("--apply", "--strict-source")
        self.assert_ok(completed)
        self.assertNotIn("profile work checkout", completed.stdout)

    def test_unmerged_dirty_profile_only_warns_by_default(self) -> None:
        self.make_profile_unmerged_and_dirty()
        completed = self.install("--apply")
        self.assert_ok(completed)
        self.assertIn("WARNING: profile work checkout is on branch feat/profile, not main", completed.stdout)
        self.assertIn("WARNING: profile work checkout has uncommitted changes", completed.stdout)
        self.assertTrue((self.home / ".claude" / "skills" / "work-skill").is_dir())

    def test_strict_source_refuses_apply(self) -> None:
        self.make_profile_unmerged_and_dirty()
        refused = self.install("--apply", "--strict-source")
        self.assert_fails(refused, code=2)
        self.assertIn("profile work checkout is on branch feat/profile", refused.stderr)
        self.assertIn("(--strict-source)", refused.stderr)
        self.assertFalse((self.home / ".claude").exists())

        dry_run = self.install("--strict-source")
        self.assert_ok(dry_run)
        self.assertIn("--apply would refuse because of --strict-source", dry_run.stdout)

    def test_framework_waivers_do_not_cover_profiles(self) -> None:
        self.make_profile_unmerged_and_dirty()
        refused = self.install("--apply", "--strict-source", "--allow-dirty", "--allow-unmerged")
        self.assert_fails(refused, code=2)
        self.assertIn("profile work checkout", refused.stderr)


if __name__ == "__main__":
    unittest.main()
