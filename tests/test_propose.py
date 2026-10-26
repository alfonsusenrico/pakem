"""pakem propose: proposals as pull requests the owner merges (accept) or closes (reject).

The fixture framework is a real git repository with a bare `origin` (reached through a
github.com URL rewritten by insteadOf); a fake gh keeps pull requests as JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys

from support import PAKEM, IsolatedTestCase, git, init_repo, isolated_env, write

FAKE_GH = r'''#!{python}
import json, sys
from pathlib import Path

state = Path({state!r})
store = state / "pulls.json"
pulls = json.loads(store.read_text()) if store.exists() else {{}}
args = sys.argv[1:]
with open(state / "calls.log", "a") as log:
    log.write("gh " + " ".join(args) + "\n")

def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default

def save():
    store.write_text(json.dumps(pulls))

if args[:2] == ["auth", "status"] or args[:1] == ["--version"]:
    print("ok"); sys.exit(0)
if args[:2] == ["label", "create"]:
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    number = str(len(pulls) + 1)
    pulls[number] = {{"number": int(number), "title": option("--title"), "body": sys.stdin.read(), "state": "OPEN",
                      "labels": [{{"name": option("--label")}}], "headRefName": option("--head"),
                      "url": f"https://github.com/me/pakem/pull/{{number}}", "comments": []}}
    save(); print(pulls[number]["url"]); sys.exit(0)
if args[:2] == ["pr", "list"]:
    words = (option("--search") or "").split()
    found = [pull for pull in pulls.values() if not words or any(w in pull["title"].lower() for w in words)]
    print(json.dumps(found)); sys.exit(0)
if args[:2] == ["pr", "view"]:
    print(json.dumps(pulls[args[2]])); sys.exit(0)
if args[:2] == ["pr", "edit"]:
    pull = pulls[args[2]]
    if "--body-file" in args:
        pull["body"] = sys.stdin.read()
    if "--title" in args:
        pull["title"] = option("--title")
    if "--add-label" in args:
        pull["labels"].append({{"name": option("--add-label")}})
    save(); sys.exit(0)
if args[:2] == ["pr", "comment"]:
    pulls[args[2]]["comments"].append(option("--body")); save(); sys.exit(0)
if args[:2] == ["pr", "close"]:
    pulls[args[2]]["state"] = "CLOSED"; save(); sys.exit(0)
print("unexpected gh call", args, file=sys.stderr); sys.exit(1)
'''
SKILL = "# Docs\n\nWrite docs.\n"
OK_TEST = "import unittest\n\n\nclass Ok(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
DIFF = """diff --git a/skills/docs/SKILL.md b/skills/docs/SKILL.md
--- a/skills/docs/SKILL.md
+++ b/skills/docs/SKILL.md
@@ -1,3 +1,5 @@
 # Docs

 Write docs.
+
+The README shows the project; details live in docs/ pages.
"""
REVISED = DIFF.replace("The README shows the project; details live in docs/ pages.", "A short README; docs/ pages hold the depth.")
BRANCH = "docs/proposal-readme-shows-the-project"


class ProposeTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.base / "gh-state"
        self.state.mkdir()
        self.bin = self.base / "bin"
        write(self.bin / "gh", FAKE_GH.format(python=sys.executable, state=str(self.state)), mode=0o755)
        write(self.framework / "skills" / "docs" / "SKILL.md", SKILL)
        write(self.framework / "tools" / "pakem", "import sys\nsys.exit(0)\n")
        write(self.framework / "tests" / "test_ok.py", OK_TEST)
        write(self.framework / ".gitignore", "__pycache__/\n")
        write(self.framework / "changes" / "README.md", "# Changes\n")
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        self.origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", str(self.origin), home=self.home)
        url = "https://github.com/me/pakem.git"
        git(self.framework, "remote", "add", "origin", url, home=self.home)
        git(self.framework, "config", f"url.{self.origin}.insteadOf", url, home=self.home)
        git(self.framework, "push", "-q", "-u", "origin", "main", home=self.home)
        self.env = isolated_env(self.home, {"PATH": f"{self.bin}:/usr/bin:/bin"})
        self.diff = write(self.base / "change.diff", DIFF)

    def propose(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "propose", *args],
            capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL, cwd=str(self.base),
        )

    def fields(self, title: str = "README shows the project") -> list[str]:
        return [
            "--title", title, "--rule", "The README shows the project; details live in docs/ pages.",
            "--case", "headquarter's README explained too much on one page.",
            "--why-general", "Every project has a README that people skim.", "--home", "skill",
            "--change-file", str(self.diff), "--evidence", "headquarter README.md, 2026-10-22",
            "--project", "headquarter", "--yes",
        ]

    def pulls(self) -> dict:
        return json.loads((self.state / "pulls.json").read_text())

    def on_origin(self, branch: str, path: str) -> str:
        return git(self.origin, "show", f"{branch}:{path}", home=self.home).stdout


class ProposeTests(ProposeTestCase):
    def without(self, fields: list[str], name: str, takes_value: bool = True) -> list[str]:
        index = fields.index(name)
        return fields[:index] + fields[index + (2 if takes_value else 1):]

    def test_nothing_is_pushed_before_the_owner_saw_the_text(self) -> None:
        completed = self.propose("new", *self.without(self.fields(), "--yes", takes_value=False))
        self.assert_fails(completed, code=2)
        self.assertIn("pass --yes", completed.stderr)
        self.assertIn("## Rule", completed.stdout, "the text is printed first")
        self.assertFalse((self.state / "pulls.json").exists())
        self.assertEqual(git(self.origin, "branch", "--list", BRANCH, home=self.home).stdout, "")

    def test_private_text_never_reaches_the_public_repository(self) -> None:
        write(self.home / ".config" / "pakem" / "private-names.txt", "# clients\nacme-corp\n")
        write(self.home / ".config" / "pakem" / "hosts.toml",
              '[hosts.homebox]\ntarget = "admin@198.18.0.7"\nrelease_port = 2223\n')
        fields = self.fields()
        cases = {
            "a private name": ("--case", "acme-corp's README explained too much.", "acme-corp"),
            "a host": ("--case", "Seen on homebox after a release.", "homebox"),
            "a release port": ("--case", "The receiver listens on port 2223.", "2223"),
            "an address": ("--evidence", "logs from 10.20.30.40", "10.20.30.40"),
            "a home path": ("--rule", "Keep notes in /Users/someone/notes.", "someone"),
        }
        for name, (flag, value, token) in cases.items():
            with self.subTest(name):
                changed = list(fields)
                changed[changed.index(flag) + 1] = value
                completed = self.propose("new", *changed)
                self.assert_fails(completed, code=6)
                self.assertIn("would publish private text", completed.stderr)
                self.assertIn("body line", completed.stderr)
                self.assertNotIn(token, completed.stderr, "the value is never printed")
        self.diff.write_text(DIFF.replace("details live in docs/ pages.", "see homebox for an example."))
        completed = self.propose("new", *fields)
        self.assert_fails(completed, code=6)
        self.assertIn("change line 9", completed.stderr)
        self.assertFalse((self.state / "pulls.json").exists())

    def test_documentation_addresses_and_a_generic_project_pass(self) -> None:
        fields = self.without(self.fields(), "--project")
        fields[fields.index("--evidence") + 1] = "a test host at 203.0.113.7, loopback 127.0.0.1"
        self.assert_ok(self.propose("new", *fields))
        self.assertIn("Proposed from a project on", self.pulls()["1"]["body"])

    def test_subjects_follow_the_change_and_stop_at_a_word(self) -> None:
        title = "Tooling for releases that keeps every device on the same page when hosts change often"
        fields = self.fields(title)
        fields[fields.index("--home") + 1] = "tooling"
        self.assert_ok(self.propose("new", *fields, "--new-subject"))
        subject = self.pulls()["1"]["title"]
        self.assertLessEqual(len(subject), 72)
        self.assertTrue(subject.startswith("feat: Tooling for releases"), subject)
        self.assertTrue(title.startswith(subject.removeprefix("feat: ")), "cut at a word, not inside one")
        self.assertEqual(title[len(subject) - len("feat: ")], " ")

    def test_new_opens_a_pull_request_with_the_case_and_the_checked_change(self) -> None:
        completed = self.propose("new", *self.fields())
        self.assert_ok(completed)
        self.assertIn("Proposal for the owner's review (merge to accept): https://github.com/me/pakem/pull/1",
                      completed.stdout)
        pull = self.pulls()["1"]
        self.assertEqual((pull["title"], pull["headRefName"]), ("docs: README shows the project", BRANCH))
        self.assertIn({"name": "proposal"}, pull["labels"])
        for part in ("Proposed from **headquarter**", "## Rule", "## Why it holds beyond this project",
                     "headquarter README.md, 2026-10-22", "Merge to accept"):
            self.assertIn(part, pull["body"])
        self.assertIn("details live in docs/ pages", self.on_origin(BRANCH, "skills/docs/SKILL.md"))
        files = git(self.origin, "show", "--name-only", "--format=", BRANCH, home=self.home).stdout.split()
        self.assertIn("changes/changed-proposal-readme-shows-the-project.md", files)
        self.assertEqual(git(self.framework, "branch", "--show-current", home=self.home).stdout.strip(), "main")
        self.assertEqual(git(self.framework, "status", "--short", home=self.home).stdout, "")
        self.assertEqual(git(self.framework, "worktree", "list", home=self.home).stdout.count("\n"), 1)

    def test_a_change_with_its_own_note_gets_no_second_one(self) -> None:
        note = ("diff --git a/changes/changed-readme-scope.md b/changes/changed-readme-scope.md\n"
                "new file mode 100644\n--- /dev/null\n+++ b/changes/changed-readme-scope.md\n"
                "@@ -0,0 +1 @@\n+- The README shows the project.\n")
        self.diff.write_text(DIFF + note)
        self.assert_ok(self.propose("new", *self.fields()))
        files = git(self.origin, "show", "--name-only", "--format=", BRANCH, home=self.home).stdout.split()
        self.assertEqual([name for name in files if name.startswith("changes/")], ["changes/changed-readme-scope.md"])
        self.diff.write_text(REVISED + note)
        self.assert_ok(self.propose("revise", "1", *self.fields()))
        changed = git(self.origin, "diff", "--name-only", "main", BRANCH, "--", "changes/", home=self.home).stdout
        self.assertEqual(changed.split(), ["changes/changed-readme-scope.md"], "a revision adds none either")

    def test_a_change_that_does_not_apply_is_refused(self) -> None:
        self.diff.write_text(DIFF.replace("Write docs.", "Something else."))
        completed = self.propose("new", *self.fields())
        self.assert_fails(completed, code=6)
        self.assertIn("does not apply to the framework's main", completed.stderr)
        self.assertFalse((self.state / "pulls.json").exists())
        self.assertEqual(git(self.framework, "worktree", "list", home=self.home).stdout.count("\n"), 1)

    def test_failing_checks_push_nothing(self) -> None:
        write(self.framework / "tests" / "test_ok.py", OK_TEST.replace("self.assertTrue(True)", "self.fail('broken')"))
        git(self.framework, "commit", "-q", "-am", "test: break", home=self.home)
        git(self.framework, "push", "-q", "origin", "main", home=self.home)
        completed = self.propose("new", *self.fields())
        self.assert_fails(completed, code=5)
        self.assertIn("broken", completed.stderr)
        self.assertFalse((self.state / "pulls.json").exists())
        self.assertTrue((self.framework.parent / "framework-proposal-readme-shows-the-project").exists())

    def test_an_earlier_proposal_on_the_subject_is_named(self) -> None:
        self.assert_ok(self.propose("new", *self.fields()))
        completed = self.propose("new", *self.fields(title="README for the project page"))
        self.assert_fails(completed, code=6)
        self.assertIn("#1 docs: README shows the project (open)", completed.stderr)

    def test_plan_checks_the_change_and_sends_nothing(self) -> None:
        completed = self.propose("new", *self.fields(), "--plan")
        self.assert_ok(completed)
        self.assertIn("## Case", completed.stdout)
        self.assertIn("the change applies to main", completed.stdout)
        self.assertFalse((self.state / "pulls.json").exists())

    def test_revise_adds_a_commit_with_the_new_change(self) -> None:
        self.assert_ok(self.propose("new", *self.fields()))
        before = git(self.origin, "rev-parse", BRANCH, home=self.home).stdout.strip()
        self.diff.write_text(REVISED)
        completed = self.propose("revise", "1", *self.fields())
        self.assert_ok(completed)
        skill = self.on_origin(BRANCH, "skills/docs/SKILL.md")
        self.assertIn("A short README; docs/ pages hold the depth.", skill)
        self.assertNotIn("details live in docs/ pages", skill)
        ancestry = git(self.origin, "merge-base", "--is-ancestor", before, BRANCH, home=self.home, check=False)
        self.assertEqual(ancestry.returncode, 0, "no history is rewritten")
        self.assertIn("Revised after the owner's review.", self.pulls()["1"]["comments"])

    def test_reject_records_the_reason_and_closes(self) -> None:
        self.assert_ok(self.propose("new", *self.fields()))
        self.assert_ok(self.propose("reject", "1", "--reason", "project-specific after all"))
        pull = self.pulls()["1"]
        self.assertEqual(pull["state"], "CLOSED")
        self.assertIn("Rejected by the owner: project-specific after all", pull["comments"])
        self.assertIn({"name": "proposal:rejected"}, pull["labels"])
        self.assert_fails(self.propose("reject", "1", "--reason", "again"), code=6)

    def test_list_and_show(self) -> None:
        self.assert_ok(self.propose("new", *self.fields()))
        self.assertIn("#1    open      docs: README shows the project", self.propose("list").stdout)
        self.assertIn("## Case", self.propose("show", "1").stdout)
