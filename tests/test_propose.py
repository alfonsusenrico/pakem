"""pakem propose: proposals as GitHub issues, accepted ones as pull requests.

The fixture framework is a real git repository with a bare `origin` (reached through a
github.com URL rewritten by insteadOf); a fake gh keeps issues and pull requests as JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from support import PAKEM, IsolatedTestCase, git, init_repo, isolated_env, write

FAKE_GH = r'''#!{python}
import json, sys
from pathlib import Path

state = Path({state!r})
issues_file = state / "issues.json"
issues = json.loads(issues_file.read_text()) if issues_file.exists() else {{}}
args = sys.argv[1:]
with open(state / "calls.log", "a") as log:
    log.write("gh " + " ".join(args) + "\n")

def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default

def save():
    issues_file.write_text(json.dumps(issues))

if args[:2] == ["auth", "status"] or args[:1] == ["--version"]:
    print("ok"); sys.exit(0)
if args[:2] == ["label", "create"]:
    sys.exit(0)
if args[:2] == ["issue", "create"]:
    number = str(len(issues) + 1)
    issues[number] = {{"number": int(number), "title": option("--title"), "body": sys.stdin.read(), "state": "OPEN",
                       "labels": [{{"name": option("--label")}}], "url": f"https://github.com/me/pakem/issues/{{number}}",
                       "comments": []}}
    save(); print(issues[number]["url"]); sys.exit(0)
if args[:2] == ["issue", "list"]:
    words = (option("--search") or "").split()
    found = [issue for issue in issues.values() if not words or any(w in issue["title"].lower() for w in words)]
    print(json.dumps(found)); sys.exit(0)
if args[:2] == ["issue", "view"]:
    print(json.dumps(issues[args[2]])); sys.exit(0)
if args[:2] == ["issue", "edit"]:
    issue = issues[args[2]]
    if "--body-file" in args:
        issue["body"] = sys.stdin.read()
    if "--title" in args:
        issue["title"] = option("--title")
    if "--add-label" in args:
        issue["labels"].append({{"name": option("--add-label")}})
    save(); sys.exit(0)
if args[:2] == ["issue", "comment"]:
    issues[args[2]]["comments"].append(option("--body")); save(); sys.exit(0)
if args[:2] == ["issue", "close"]:
    issues[args[2]]["state"] = "CLOSED"; save(); sys.exit(0)
if args[:2] == ["pr", "create"]:
    (state / "pr.json").write_text(json.dumps({{"head": option("--head"), "body": option("--body")}}))
    print("https://github.com/me/pakem/pull/9"); sys.exit(0)
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

    def new(self, *extra: str, title: str = "README shows the project") -> subprocess.CompletedProcess:
        return self.propose(
            "new", "--title", title, "--rule", "The README shows the project; details live in docs/ pages.",
            "--case", "headquarter's README explained too much on one page.",
            "--why-general", "Every project has a README that people skim.", "--home", "skill",
            "--change-file", str(self.diff), "--evidence", "headquarter README.md, 2026-10-22",
            "--project", "headquarter", *extra,
        )

    def issues(self) -> dict:
        return json.loads((self.state / "issues.json").read_text())


class ProposeTests(ProposeTestCase):
    def test_new_opens_an_issue_with_the_case_and_the_exact_change(self) -> None:
        completed = self.new()
        self.assert_ok(completed)
        self.assertIn("https://github.com/me/pakem/issues/1", completed.stdout)
        issue = self.issues()["1"]
        self.assertEqual(issue["title"], "proposal: README shows the project")
        for part in ("Proposed from **headquarter**", "## Rule", "## Why it holds beyond this project",
                     "headquarter README.md, 2026-10-22", "```diff\ndiff --git a/skills/docs/SKILL.md"):
            self.assertIn(part, issue["body"])
        self.assertEqual(git(self.framework, "worktree", "list", home=self.home).stdout.count("\n"), 1,
                         "the check worktree is removed")

    def test_a_change_that_does_not_apply_is_refused(self) -> None:
        self.diff.write_text(DIFF.replace("Write docs.", "Something else."))
        completed = self.new()
        self.assert_fails(completed, code=6)
        self.assertIn("does not apply to the framework's main", completed.stderr)
        self.assertFalse((self.state / "issues.json").exists())

    def test_an_earlier_proposal_on_the_subject_is_named(self) -> None:
        self.assert_ok(self.new())
        completed = self.new(title="README for the project page")
        self.assert_fails(completed, code=6)
        self.assertIn("#1 proposal: README shows the project (open)", completed.stderr)
        self.assert_ok(self.new("--new-subject", title="README for the project page"))

    def test_plan_sends_nothing(self) -> None:
        completed = self.new("--plan")
        self.assert_ok(completed)
        self.assertIn("## Change", completed.stdout)
        self.assertFalse((self.state / "issues.json").exists())

    def test_accept_turns_it_into_a_pull_request_from_its_own_worktree(self) -> None:
        self.assert_ok(self.new())
        completed = self.propose("accept", "1")
        self.assert_ok(completed)
        self.assertIn("Pull request for the owner to merge: https://github.com/me/pakem/pull/9", completed.stdout)
        self.assertIn("run `pakem update`", completed.stdout)
        pr = json.loads((self.state / "pr.json").read_text())
        self.assertEqual(pr["head"], "docs/proposal-1-readme-shows-the-project")
        self.assertIn("Closes #1.", pr["body"])
        pushed = git(self.origin, "show", f"{pr['head']}:skills/docs/SKILL.md", home=self.home).stdout
        self.assertIn("details live in docs/ pages", pushed)
        files = git(self.origin, "show", "--name-only", "--format=", pr["head"], home=self.home).stdout.split()
        self.assertIn("changes/changed-proposal-1-readme-shows-the-project.md", files)
        issue = self.issues()["1"]
        self.assertIn({"name": "proposal:accepted"}, issue["labels"])
        self.assertEqual(git(self.framework, "branch", "--show-current", home=self.home).stdout.strip(), "main")
        self.assertEqual(git(self.framework, "status", "--short", home=self.home).stdout, "")
        self.assertFalse((self.framework.parent / "framework-proposal-1").exists(), "the worktree is removed")

    def test_failing_checks_stop_before_anything_is_pushed(self) -> None:
        write(self.framework / "tests" / "test_ok.py", OK_TEST.replace("self.assertTrue(True)", "self.fail('broken')"))
        git(self.framework, "commit", "-q", "-am", "test: break", home=self.home)
        git(self.framework, "push", "-q", "origin", "main", home=self.home)
        self.assert_ok(self.new())
        completed = self.propose("accept", "1")
        self.assert_fails(completed, code=5)
        self.assertIn("failed on the proposal branch", completed.stderr)
        self.assertIn("broken", completed.stderr)
        self.assertFalse((self.state / "pr.json").exists())
        self.assertTrue((self.framework.parent / "framework-proposal-1").exists(), "kept for a look")

    def test_reject_records_the_reason_and_closes(self) -> None:
        self.assert_ok(self.new())
        self.assert_ok(self.propose("reject", "1", "--reason", "project-specific after all"))
        issue = self.issues()["1"]
        self.assertEqual(issue["state"], "CLOSED")
        self.assertIn("Rejected by the owner: project-specific after all", issue["comments"])
        self.assert_fails(self.propose("accept", "1"), code=6)

    def test_revise_replaces_the_text(self) -> None:
        self.assert_ok(self.new())
        self.assert_ok(self.propose(
            "revise", "1", "--title", "README presents the project", "--rule", "Short README; docs/ pages for depth.",
            "--case", "same", "--why-general", "same", "--home", "template", "--change-file", str(self.diff),
        ))
        issue = self.issues()["1"]
        self.assertEqual(issue["title"], "proposal: README presents the project")
        self.assertIn("Short README; docs/ pages for depth.", issue["body"])
        self.assertIn("Revised after the owner's review.", issue["comments"])

    def test_list_and_show(self) -> None:
        self.assert_ok(self.new())
        listing = self.propose("list")
        self.assertIn("#1    open      proposal: README shows the project", listing.stdout)
        self.assertIn("## Case", self.propose("show", "1").stdout)
