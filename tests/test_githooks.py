"""commit-msg and pre-push hooks, plus af hooks install, in temporary git repos."""

from __future__ import annotations

import re
import subprocess
import unittest

from support import HOOKS_DIR, IsolatedTestCase, git, init_repo, isolated_env, write

PATTERN_LINE = re.compile(r"^SUBJECT_PATTERN=(.*)$", re.MULTILINE)


def subject_pattern(hook_name: str) -> str:
    match = PATTERN_LINE.search((HOOKS_DIR / hook_name).read_text())
    assert match is not None, f"SUBJECT_PATTERN missing from {hook_name}"
    return match.group(1)


class SubjectPatternTests(unittest.TestCase):
    def test_both_hooks_share_the_same_pattern(self) -> None:
        self.assertEqual(subject_pattern("commit-msg"), subject_pattern("pre-push"))


class CommitMsgTests(IsolatedTestCase):
    def run_hook(self, message: str) -> subprocess.CompletedProcess:
        message_file = write(self.base / "COMMIT_EDITMSG", message)
        return subprocess.run(
            ["/bin/sh", str(HOOKS_DIR / "commit-msg"), str(message_file)],
            capture_output=True,
            text=True,
            env=isolated_env(self.home),
        )

    def test_accepts_valid_subjects(self) -> None:
        for message in (
            "feat: add login\n",
            "fix(api): handle empty body\n",
            "refactor!: drop legacy flag\n",
            "docs: update readme\n\n# Please enter the commit message\n",
            "chore: tidy\n# ------------------------ >8 ------------------------\ndiff --git a b\n+body text\n",
            "Merge branch 'feat/x'\n\nConflicts resolved.\n",
            'Revert "feat: add login"\n\nThis reverts commit abc.\n',
        ):
            with self.subTest(message=message):
                completed = self.run_hook(message)
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_invalid_messages(self) -> None:
        cases = {
            "feat: add login\n\nWith a body.\n": "body or trailers",
            "feat: add login\n\nCo-Authored-By: Bot <bot@example.invalid>\n": "body or trailers",
            "feature: add login\n": "conventional commit format",
            "feat:missing space\n": "conventional commit format",
            "feat: " + "x" * 70 + "\n": "limit 72",
            "\n\n": "empty commit message",
        }
        for message, needle in cases.items():
            with self.subTest(message=message):
                completed = self.run_hook(message)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(needle, completed.stderr)
                self.assertIn("allowed types: feat fix docs", completed.stderr)


class PrePushTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.remote = self.base / "remote.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote), home=self.home)
        self.repo = init_repo(self.base / "work", self.home)
        git(self.repo, "remote", "add", "origin", str(self.remote), home=self.home)
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        (hooks / "pre-push").write_bytes((HOOKS_DIR / "pre-push").read_bytes())
        (hooks / "pre-push").chmod(0o755)
        git(self.repo, "config", "core.hooksPath", ".githooks", home=self.home)
        self.commit("README.md", "seed\n", "docs: seed readme")
        self.push("main", hooks=False)

    def commit(self, relative: str, content: str, message: str) -> None:
        write(self.repo / relative, content)
        git(self.repo, "add", relative, home=self.home)
        git(self.repo, "commit", "-q", "-m", message, home=self.home)

    def push(
        self, refspec: str, hooks: bool = True, extra_env: dict | None = None
    ) -> subprocess.CompletedProcess:
        arguments = ["push", "-q", "origin", refspec]
        if not hooks:
            arguments.insert(1, "--no-verify")
        return git(self.repo, *arguments, home=self.home, check=False, extra_env=extra_env)

    def test_docs_only_push_to_main_is_allowed(self) -> None:
        self.commit("docs/guide.md", "guide\n", "docs: add guide")
        self.commit("CHANGELOG.md", "log\n", "docs: add changelog")
        completed = self.push("main")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_code_push_to_main_is_rejected(self) -> None:
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("only *.md and docs/** may be pushed to main", completed.stderr)

    def test_code_push_to_topic_branch_is_allowed(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "feat/app", home=self.home)
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("feat/app")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_bypass_variable_only_skips_branch_rule(self) -> None:
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main", extra_env={"AF_ALLOW_MAIN_PUSH": "1"})
        self.assertEqual(completed.returncode, 0, completed.stderr)

        self.commit("src/other.py", "x = 1\n", "bad subject")
        rejected = self.push("main", extra_env={"AF_ALLOW_MAIN_PUSH": "1"})
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("is not a conventional commit", rejected.stderr)

    def test_bypass_alias_allows_code_push_to_main(self) -> None:
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main", extra_env={"AF_ALLOW_PROTECTED_PUSH": "1"})
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_unconfigured_repository_does_not_protect_other_branches(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "development", home=self.home)
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("development")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def protect(self, *branches: str) -> None:
        for branch in branches:
            git(self.repo, "config", "--add", "agentframework.protectedBranch", branch, home=self.home)

    def test_no_protected_branch_allows_code_push_to_main(self) -> None:
        self.protect("-")
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_configured_branch_rejects_code_push(self) -> None:
        self.protect("development")
        git(self.repo, "checkout", "-q", "-b", "development", home=self.home)
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        for refspec in ("development", "HEAD:refs/heads/development"):
            with self.subTest(refspec=refspec):
                completed = self.push(refspec)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("only *.md and docs/** may be pushed to development", completed.stderr)

    def test_configured_branch_accepts_docs_only_push(self) -> None:
        self.protect("development")
        git(self.repo, "checkout", "-q", "-b", "development", home=self.home)
        self.commit("docs/guide.md", "guide\n", "docs: add guide")
        completed = self.push("development")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_configured_set_replaces_the_default(self) -> None:
        self.protect("development")
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_configured_set_can_keep_main(self) -> None:
        self.protect("development", "main")
        self.commit("src/app.py", "print('x')\n", "feat: add app")
        completed = self.push("main")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("may be pushed to main", completed.stderr)

    def test_overrides_allow_code_push_to_configured_branch(self) -> None:
        self.protect("development")
        git(self.repo, "checkout", "-q", "-b", "development", home=self.home)
        for variable in ("AF_ALLOW_PROTECTED_PUSH", "AF_ALLOW_MAIN_PUSH"):
            with self.subTest(variable=variable):
                self.commit(f"src/{variable}.py", "x = 1\n", "feat: add module")
                completed = self.push("development", extra_env={variable: "1"})
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_body_is_rejected(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "feat/body", home=self.home)
        write(self.repo / "src" / "a.py", "a = 1\n")
        git(self.repo, "add", "src/a.py", home=self.home)
        git(
            self.repo,
            "commit",
            "-q",
            "-m",
            "feat: add a",
            "-m",
            "Co-Authored-By: Bot <bot@example.invalid>",
            home=self.home,
        )
        completed = self.push("feat/body")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("no body or trailers", completed.stderr)

    def test_fixup_is_rejected(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "feat/fixup", home=self.home)
        self.commit("src/a.py", "a = 1\n", "fixup! feat: add a")
        completed = self.push("feat/fixup")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("autosquash commit", completed.stderr)

    def test_branch_deletion_is_skipped(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "feat/tmp", home=self.home)
        self.commit("src/a.py", "a = 1\n", "feat: add a")
        self.assertEqual(self.push("feat/tmp").returncode, 0)
        deleted = self.push(":feat/tmp")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)


class HooksInstallTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = init_repo(self.base / "app", self.home)

    def test_dry_run_then_apply(self) -> None:
        dry_run = self.af("hooks", "install", str(self.repo))
        self.assert_ok(dry_run)
        self.assertFalse((self.repo / ".githooks").exists())

        self.assert_ok(self.af("hooks", "install", str(self.repo), "--apply"))
        for name in ("commit-msg", "pre-push"):
            installed = self.repo / ".githooks" / name
            self.assertEqual(installed.read_text(), (self.framework / "hooks" / name).read_text())
            self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        configured = git(self.repo, "config", "--get", "core.hooksPath", home=self.home).stdout.strip()
        self.assertEqual(configured, ".githooks")

        again = self.af("hooks", "install", str(self.repo), "--apply")
        self.assert_ok(again)
        self.assertIn("unchanged", again.stdout)

    def test_refuses_existing_hooks_path(self) -> None:
        git(self.repo, "config", "core.hooksPath", "tools/hooks", home=self.home)
        completed = self.af("hooks", "install", str(self.repo), "--apply")
        self.assert_fails(completed)
        self.assertIn("core.hooksPath is already set", completed.stdout)
        self.assertFalse((self.repo / ".githooks").exists())

    def protected_values(self, repo=None) -> list[str]:
        completed = git(
            repo or self.repo, "config", "--get-all", "agentframework.protectedBranch", home=self.home, check=False
        )
        return completed.stdout.splitlines()

    def exclude_lines(self) -> list[str]:
        exclude = self.repo / ".git" / "info" / "exclude"
        if not exclude.exists():
            return []
        return exclude.read_text().splitlines()

    def test_protect_dry_run_writes_nothing(self) -> None:
        completed = self.af("hooks", "install", str(self.repo), "--protect", "development")
        self.assert_ok(completed)
        self.assertIn("agentframework.protectedBranch: development (currently unset, so main, master)", completed.stdout)
        self.assertEqual(self.protected_values(), [])

    def test_protect_is_idempotent_and_replaces_values(self) -> None:
        arguments = ("hooks", "install", str(self.repo), "--protect", "development", "--protect", "main")
        self.assert_ok(self.af(*arguments, "--apply"))
        self.assertEqual(self.protected_values(), ["development", "main"])

        again = self.af(*arguments, "--protect", "development", "--apply")
        self.assert_ok(again)
        self.assertIn("unchanged               git config agentframework.protectedBranch", again.stdout)
        self.assertEqual(self.protected_values(), ["development", "main"])

        narrowed = self.af("hooks", "install", str(self.repo), "--protect", "development", "--apply")
        self.assert_ok(narrowed)
        self.assertEqual(self.protected_values(), ["development"])

    def test_protect_rejects_invalid_names(self) -> None:
        for name in ("bad..name", "refs/heads/development", "-x"):
            with self.subTest(name=name):
                completed = self.af("hooks", "install", str(self.repo), f"--protect={name}", "--apply")
                self.assert_fails(completed, code=2)
                self.assertFalse((self.repo / ".githooks").exists())
        self.assertEqual(self.protected_values(), [])

    def test_protect_none_allows_code_push_to_main(self) -> None:
        completed = self.af("hooks", "install", str(self.repo), "--protect-none", "--apply")
        self.assert_ok(completed)
        self.assertIn("none (direct pushes allowed)", completed.stdout)
        self.assertEqual(self.protected_values(), ["-"])
        self.assert_fails(
            self.af("hooks", "install", str(self.repo), "--protect-none", "--protect", "main", "--apply"), code=2
        )

    def test_without_protect_leaves_config_alone(self) -> None:
        git(self.repo, "config", "--add", "agentframework.protectedBranch", "development", home=self.home)
        completed = self.af("hooks", "install", str(self.repo), "--apply")
        self.assert_ok(completed)
        self.assertNotIn("protectedBranch", completed.stdout)
        self.assertEqual(self.protected_values(), ["development"])

    def test_local_only_excludes_hooks_once(self) -> None:
        dry_run = self.af("hooks", "install", str(self.repo), "--local-only")
        self.assert_ok(dry_run)
        self.assertIn(".githooks/ to .git/info/exclude", dry_run.stdout)
        self.assertNotIn(".githooks/", self.exclude_lines())

        self.assert_ok(self.af("hooks", "install", str(self.repo), "--local-only", "--apply"))
        again = self.af("hooks", "install", str(self.repo), "--local-only", "--apply")
        self.assert_ok(again)
        self.assertIn(".git/info/exclude excludes .githooks/", again.stdout)
        self.assertEqual(self.exclude_lines().count(".githooks/"), 1)
        status = git(self.repo, "status", "--porcelain", "--untracked-files=all", home=self.home).stdout
        self.assertNotIn(".githooks", status)

    def test_local_only_appends_to_existing_exclude(self) -> None:
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_text("# local\n*.log")
        self.assert_ok(self.af("hooks", "install", str(self.repo), "--local-only", "--apply"))
        self.assertEqual(exclude.read_text(), "# local\n*.log\n.githooks/\n")

    def test_local_only_shares_exclude_with_linked_worktree(self) -> None:
        write(self.repo / "README.md", "seed\n")
        git(self.repo, "add", "README.md", home=self.home)
        git(self.repo, "commit", "-q", "-m", "docs: seed", home=self.home)
        worktree = self.base / "app-feature"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/x", str(worktree), home=self.home)

        self.assert_ok(self.af("hooks", "install", str(self.repo), "--local-only", "--apply"))
        self.assert_ok(self.af("hooks", "install", str(worktree), "--local-only", "--apply"))
        self.assertTrue((worktree / ".githooks" / "pre-push").exists())
        self.assertEqual(self.exclude_lines().count(".githooks/"), 1)
        status = git(worktree, "status", "--porcelain", "--untracked-files=all", home=self.home).stdout
        self.assertNotIn(".githooks", status)

    def test_local_only_refuses_tracked_hooks(self) -> None:
        write(self.repo / ".githooks" / "pre-push", "#!/bin/sh\n", mode=0o755)
        git(self.repo, "add", ".githooks/pre-push", home=self.home)
        completed = self.af("hooks", "install", str(self.repo), "--local-only", "--force", "--apply")
        self.assert_fails(completed)
        self.assertIn(".githooks/ is tracked", completed.stdout)
        self.assertNotIn(".githooks/", self.exclude_lines())
        self.assertEqual((self.repo / ".githooks" / "pre-push").read_text(), "#!/bin/sh\n")

    def build_framework(self, root, **target_options):
        root = super().build_framework(root, **target_options)
        for name in ("commit-msg", "pre-push"):
            write(root / "hooks" / name, (HOOKS_DIR / name).read_text(), mode=0o755)
        return root


if __name__ == "__main__":
    unittest.main()
