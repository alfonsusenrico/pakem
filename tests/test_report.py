"""af report: read-only Markdown drift report, latest.md, pruning, and config errors."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from support import REPO_ROOT, IsolatedTestCase, git, init_repo, write

REPORT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.md$")
SECRET_VALUE = "sk-test-0123456789abcdef"


class ReportTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.org = self.base / "org"
        self.clean = init_repo(self.org / "clean", self.home)
        self.assert_ok(self.af("project", "sync", str(self.clean), "--apply"))
        (self.clean / "research").mkdir()
        self.broken = init_repo(self.org / "broken", self.home)
        write(self.broken / ".env", f"API_KEY={SECRET_VALUE}\n")
        write(self.org / "notes.txt", "not a repository\n")
        self.output = self.base / "reports"
        self.config = self.write_config(f'repos_dirs = ["{self.org}"]\n')

    def write_config(self, text: str, path: Path | None = None) -> Path:
        return write(path or self.base / "report.toml", text)

    def report(self, *extra: str, config: Path | None = None):
        return self.af("report", "--config", str(config or self.config), "--output-dir", str(self.output), *extra)

    def written_reports(self) -> list[str]:
        return sorted(path.name for path in self.output.iterdir() if REPORT_NAME.match(path.name))

    def test_report_summarizes_repos_and_writes_latest(self) -> None:
        completed = self.report()
        self.assert_ok(completed)
        names = self.written_reports()
        self.assertEqual(len(names), 1)
        report_path = self.output / names[0]
        self.assertIn(str(report_path), completed.stdout)
        self.assertIn("af report: 2 repo(s)", completed.stdout)

        text = report_path.read_text()
        self.assertEqual(text, (self.output / "latest.md").read_text())
        self.assertIn("# Agent framework drift report", text)
        self.assertIn(f"| {self.clean} | ok | 0 | 0 |", text)
        self.assertIn(f"| {self.broken} | ERRORS | 1 |", text)
        self.assertIn(f"### {self.broken}", text)
        self.assertIn("ERROR: AGENTS.md not found", text)
        self.assertIn("| af lint |", text)
        self.assertIn("| af doctor: installed state |", text)
        self.assertIn("nothing installed according to the manifest", text)
        self.assertIn("not a git work tree", text)
        self.assertNotIn(SECRET_VALUE, text)
        self.assertNotIn("Project notes.", text)

    def test_report_names_framework_commit(self) -> None:
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        head = git(self.framework, "rev-parse", "HEAD", home=self.home).stdout.strip()
        self.assert_ok(self.report())
        text = (self.output / "latest.md").read_text()
        self.assertIn(f"main @ {head[:12]}; no origin remote; merge state not checked", text)

    def test_report_is_read_only_for_checked_repos(self) -> None:
        before = self.snapshot(self.org)
        self.assert_ok(self.report())
        self.assertEqual(before, self.snapshot(self.org))

    def test_explicit_repos_and_dir_that_is_a_repo(self) -> None:
        single = init_repo(self.base / "single", self.home)
        config = self.write_config(f'repos_dirs = ["{self.clean}"]\nrepos = ["{single}", "{self.clean}"]\n')
        completed = self.report(config=config)
        self.assert_ok(completed)
        self.assertIn("af report: 2 repo(s)", completed.stdout)
        text = (self.output / "latest.md").read_text()
        self.assertEqual(text.count(f"### {self.clean}\n"), 1)
        self.assertIn(f"### {single}\n", text)

    def test_missing_repo_paths_are_findings_not_failures(self) -> None:
        config = self.write_config(
            f'repos_dirs = ["{self.base / "absent"}"]\nrepos = ["{self.base / "gone"}"]\n'
        )
        completed = self.report(config=config)
        self.assert_ok(completed)
        text = (self.output / "latest.md").read_text()
        self.assertIn("| Configuration | ERRORS | 2 | 0 |", text)
        self.assertIn("repos_dirs entry not found", text)
        self.assertIn("repo not found", text)

    def test_invalid_profile_is_reported_in_its_sections(self) -> None:
        config = self.write_config(f'repos_dirs = []\nprofiles = ["{self.base / "no-profile"}"]\n')
        completed = self.report(config=config)
        self.assert_ok(completed)
        text = (self.output / "latest.md").read_text()
        self.assertIn("af doctor could not run: missing profile.toml", text)
        self.assertIn("| af lint | ERRORS |", text)

    def test_prunes_oldest_reports_only(self) -> None:
        old_names = [f"2020-01-0{day}-0800.md" for day in range(1, 6)]
        for name in old_names:
            write(self.output / name, "old report\n")
        unrelated = ["notes.md", "launchd.log", "2020-01-01-0800.md.bak", "2020-1-1-800.md"]
        for name in unrelated:
            write(self.output / name, "keep me\n")

        completed = self.report("--keep", "3")
        self.assert_ok(completed)
        self.assertIn("Pruned 3 old report(s) (keep 3).", completed.stdout)
        remaining = self.written_reports()
        self.assertEqual(remaining[:2], old_names[3:])
        self.assertEqual(len(remaining), 3)
        for name in [*unrelated, "latest.md"]:
            self.assertTrue((self.output / name).is_file(), name)

    def test_keep_comes_from_config(self) -> None:
        for day in range(1, 4):
            write(self.output / f"2020-01-0{day}-0800.md", "old report\n")
        config = self.write_config(f'repos_dirs = ["{self.org}"]\nkeep = 2\n')
        self.assert_ok(self.report(config=config))
        self.assertEqual(len(self.written_reports()), 2)
        self.assertIn("2020-01-03-0800.md", self.written_reports())

    def test_default_config_and_output_locations(self) -> None:
        write(self.home / ".config" / "agent-framework" / "report.toml", f'repos_dirs = ["{self.org}"]\n')
        completed = self.af("report")
        self.assert_ok(completed)
        reports = self.home / ".local" / "state" / "agent-framework" / "reports"
        self.assertTrue((reports / "latest.md").is_file())
        self.assertIn(str(reports), completed.stdout)

    def test_missing_config_explains_with_example(self) -> None:
        completed = self.af("report")
        self.assert_fails(completed, code=2)
        self.assertIn("report config not found: ~/.config/agent-framework/report.toml", completed.stderr)
        self.assertIn("repos_dirs = [", completed.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_invalid_config_is_rejected(self) -> None:
        cases = {
            "unknown key": ('repos_dirs = []\nrepo = ["~/x"]\n', "unknown key(s) repo"),
            "missing repos_dirs": ('repos = ["~/x"]\n', "'repos_dirs' is required"),
            "keep zero": ("repos_dirs = []\nkeep = 0\n", "'keep' must be a whole number"),
            "keep bool": ("repos_dirs = []\nkeep = true\n", "'keep' must be a whole number"),
            "relative path": ('repos_dirs = ["projects"]\n', "path must be absolute or start with ~/"),
            "not a list": ('repos_dirs = "~/projects"\n', "'repos_dirs' must be a list"),
            "bad toml": ("repos_dirs = [\n", "invalid TOML"),
        }
        for label, (text, message) in cases.items():
            with self.subTest(label):
                config = self.write_config(text)
                completed = self.report(config=config)
                self.assert_fails(completed, code=2)
                self.assertIn(message, completed.stderr)
                self.assertFalse(self.output.exists())

    def test_unwritable_output_fails(self) -> None:
        write(self.output, "a file where the directory should be\n")
        completed = self.report()
        self.assert_fails(completed, code=2)
        self.assertIn("could not write the report", completed.stderr)

    def test_doctor_repos_reports_the_same_repo_findings(self) -> None:
        completed = self.af("doctor", "--repos", str(self.org))
        self.assert_fails(completed, code=1)
        self.assertIn("Project repos (2):", completed.stdout)
        self.assertIn(f"ok        {self.clean}  (0 error(s), 0 warning(s))", completed.stdout)
        self.assertIn(f"ERRORS    {self.broken}  (1 error(s), 0 warning(s))", completed.stdout)
        self.assertIn("ERROR: broken: AGENTS.md not found", completed.stdout)

    def test_tracked_example_config_is_valid(self) -> None:
        completed = self.report(config=REPO_ROOT / "docs" / "report.example.toml")
        self.assert_ok(completed)
        text = (self.output / "latest.md").read_text()
        self.assertIn("repos_dirs entry not found: ~/projects/org", text)


if __name__ == "__main__":
    unittest.main()
