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

    def build_framework(self, root, **target_options):
        root = super().build_framework(root, **target_options)
        for name in ("commit-msg", "pre-push"):
            write(root / "hooks" / name, (HOOKS_DIR / name).read_text(), mode=0o755)
        return root


if __name__ == "__main__":
    unittest.main()
