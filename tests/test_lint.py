"""af lint rules, each exercised against a temporary framework checkout."""

from __future__ import annotations

import unittest

from support import FIXTURE_REFERENCE, FIXTURE_SKILL, IsolatedTestCase, init_repo, targets_toml, write


class LintTests(IsolatedTestCase):
    def lint(self):
        return self.af("lint")

    def assert_lint_error(self, needle: str) -> None:
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn(needle, completed.stdout)

    def skill_file(self):
        return self.framework / "skills" / "demo-skill" / "SKILL.md"

    def test_fixture_framework_is_clean(self) -> None:
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("0 error(s), 0 warning(s)", completed.stdout)

    def test_git_excluded_local_files_are_not_linted(self) -> None:
        init_repo(self.framework, self.home)
        local_state = self.framework / "PROJECT_STATE.md"
        write(local_state, "# State\n\nWorkspace at /Users/someone/project.\n")
        self.assert_lint_error("absolute-home-path")

        exclude_file = self.framework / ".git" / "info" / "exclude"
        exclude_file.write_text(exclude_file.read_text() + "PROJECT_STATE.md\n")
        completed = self.lint()
        self.assert_ok(completed)
        self.assertNotIn("PROJECT_STATE.md", completed.stdout)

    def test_kernel_size_thresholds(self) -> None:
        kernel = self.framework / "kernel" / "KERNEL.md"
        original = kernel.read_text()
        kernel.write_text(original + "x" * 9_600 + "\n")
        warned = self.lint()
        self.assert_ok(warned)
        self.assertIn("warning above 9500", warned.stdout)
        kernel.write_text(original + "x" * 10_600 + "\n")
        self.assert_lint_error("limit 10500")
        kernel.write_text(original + "line\n" * 160)
        self.assertIn("warning above 150", self.lint().stdout)

    def test_rendered_kernel_exceeding_host_limit(self) -> None:
        write(self.framework / "hosts" / "targets.toml", targets_toml(claude_max_chars=100))
        self.assert_lint_error("claude allows 100")

    def test_digest_must_be_subset_of_kernel(self) -> None:
        digest = self.framework / "kernel" / "DIGEST.md"
        digest.write_text(digest.read_text() + "A rule that is not in the kernel.\n")
        self.assert_lint_error("DIGEST.md line 4 does not appear verbatim in KERNEL.md")

    def test_digest_size_limit(self) -> None:
        digest = self.framework / "kernel" / "DIGEST.md"
        digest.write_text("<!-- " + "x" * 2_500 + " -->\n")
        self.assert_lint_error("limit 2400")

    def test_frontmatter_format_errors(self) -> None:
        cases = [
            (["name: demo-skill", "description: ok", "tags: [a, b]"], "unsupported key 'tags'"),
            (["name: demo-skill", "description: has: colon"], "contains ': '"),
            (["name: demo-skill", "description: [list]"], "YAML indicator"),
            (["name: demo-skill", "", "description: ok"], "expected 'key: value'"),
            (["name: demo-skill"], "missing required key 'description'"),
            (["name: demo-skill", "description: ok", "metadata:", "    deep: x"], "expected 'key: value'"),
        ]
        for lines, needle in cases:
            with self.subTest(lines=lines):
                self.skill_file().write_text("---\n" + "\n".join(lines) + "\n---\n# Body\n")
                self.assert_lint_error(needle)
        self.skill_file().write_text("# No frontmatter\n")
        self.assert_lint_error("first line must be '---'")

    def test_quoted_description_is_accepted(self) -> None:
        text = FIXTURE_SKILL.replace(
            "description: Demonstrates a valid skill for tests.",
            'description: "Use when: a \\"quoted\\" colon appears."',
        )
        self.skill_file().write_text(text)
        self.assert_ok(self.lint())

    def test_name_rules(self) -> None:
        self.skill_file().write_text(FIXTURE_SKILL.replace("name: demo-skill", "name: other-skill"))
        self.assert_lint_error("does not equal directory name")
        self.skill_file().write_text(FIXTURE_SKILL.replace("name: demo-skill", "name: Demo_Skill"))
        self.assert_lint_error("must match")

    def test_description_limits(self) -> None:
        long_description = "d" * 400
        self.skill_file().write_text(
            FIXTURE_SKILL.replace("Demonstrates a valid skill for tests.", long_description)
        )
        warned = self.lint()
        self.assert_ok(warned)
        self.assertIn("warning above 350", warned.stdout)
        self.skill_file().write_text(
            FIXTURE_SKILL.replace("Demonstrates a valid skill for tests.", "d" * 1_100)
        )
        self.assert_lint_error("limit 1024")

    def test_body_limits(self) -> None:
        self.skill_file().write_text(FIXTURE_SKILL + "line\n" * 510)
        self.assert_lint_error("lines (limit 500)")
        self.skill_file().write_text(FIXTURE_SKILL + ("y" * 200 + "\n") * 110)
        self.assert_lint_error("limit 20000")

    def test_skill_links_must_stay_inside_skill(self) -> None:
        write(self.framework / "docs" / "outside.md", "# Outside\n")
        self.skill_file().write_text(
            FIXTURE_SKILL + "\n[out](../../docs/outside.md)\n[gone](references/missing.md)\n"
        )
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("leaves the skill directory", completed.stdout)
        self.assertIn("does not resolve to a file", completed.stdout)

    def test_reference_to_reference_warns(self) -> None:
        references = self.framework / "skills" / "demo-skill" / "references"
        write(references / "second.md", FIXTURE_REFERENCE)
        (references / "guide.md").write_text(FIXTURE_REFERENCE + "\nSee [second](second.md).\n")
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("keep references one level deep", completed.stdout)

    def test_status_headers(self) -> None:
        references = self.framework / "skills" / "demo-skill" / "references"
        (references / "guide.md").write_text("# Guide\n\nNo headers.\n")
        self.assert_lint_error("missing 'Status:' line")
        (references / "guide.md").write_text(FIXTURE_REFERENCE.replace("2999-01-01", "2020-01-01"))
        self.assert_lint_error("review overdue")
        write(self.framework / "standards" / "README.md", "# Standards\n")
        write(self.framework / "standards" / "style.md", "# Style\n")
        (references / "guide.md").write_text(FIXTURE_REFERENCE)
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("standards/style.md: missing 'Status:'", completed.stdout)
        self.assertNotIn("standards/README.md", completed.stdout)

    def test_repo_markdown_links(self) -> None:
        write(
            self.framework / "docs" / "guide.md",
            "[ok](../kernel/KERNEL.md) [web](https://example.com) [anchor](#top) [mail](mailto:a@b.c)\n"
            "`[code](nope.md)`\n```\n[fenced](nope.md)\n```\n[broken](missing.md#part)\n",
        )
        write(self.framework / "archive" / "old.md", "[broken](nowhere.md)\n")
        write(self.framework / "tests" / "fixtures" / "x.md", "[broken](nowhere.md)\n")
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("docs/guide.md:6: broken link 'missing.md#part'", completed.stdout)
        self.assertNotIn("nope.md", completed.stdout)
        self.assertNotIn("archive/old.md", completed.stdout)
        self.assertNotIn("tests/fixtures", completed.stdout)

    def test_banned_patterns(self) -> None:
        home_like = "/" + "Users/someone/"
        write(
            self.framework / "docs" / "notes.md",
            f"path {home_like}x\nrun cut -d= -f1 .env\nthe Caveman skill\n",
        )
        write(self.framework / "docs" / "adr" / "0001.md", "We retired caveman.\n")
        write(self.framework / "hosts" / "notes.md", f"{home_like}allowed\n")
        write(self.framework / "archive" / "x.md", "caveman\n")
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("docs/notes.md:1: [absolute-home-path]", completed.stdout)
        self.assertIn("docs/notes.md:2: [cut-env-values]", completed.stdout)
        self.assertIn("docs/notes.md:3: [retired-skill-names]", completed.stdout)
        self.assertNotIn("docs/adr", completed.stdout)
        self.assertNotIn("hosts/notes.md", completed.stdout)
        self.assertNotIn("archive/", completed.stdout)

    def test_template_size_warning(self) -> None:
        write(self.framework / "skills" / "project-bootstrap" / "assets" / "project" / "AGENTS.md.tmpl", "# T\n" + "x" * 8_100 + "\n")
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("AGENTS.md.tmpl is", completed.stdout)


    def test_capability_registry_rules(self) -> None:
        registry = self.framework / "standards" / "capabilities.toml"
        cases = [
            (
                '[capabilities.database]\nsummary = "x"\nstandards = ["y"]\n',
                "[capabilities.always] is missing",
            ),
            ('[capabilities.always]\nstandards = ["y"]\n', "needs a non-empty 'summary'"),
            ('[capabilities.always]\nsummary = "x"\nstandards = []\n', "needs a non-empty 'standards' list"),
            ('[capabilities.always]\nsummary = "x"\nstandards = [""]\n', "standards[0] must be a non-empty"),
            (
                '[capabilities.always]\nsummary = "x"\nstandards = ["y"]\n'
                '[capabilities.Bad_Name]\nsummary = "x"\nstandards = ["y"]\n',
                "name must match",
            ),
            ("[capabilities\n", "invalid TOML"),
        ]
        for text, needle in cases:
            with self.subTest(needle=needle):
                registry.write_text(text)
                self.assert_lint_error(needle)
        registry.unlink()
        self.assert_lint_error("standards/capabilities.toml is missing")

    def test_total_skill_description_budget(self) -> None:
        description = "Use when " + "d" * 330 + "."
        for index in range(24):
            name = f"budget-skill-{index}"
            write(
                self.framework / "skills" / name / "SKILL.md",
                f"---\nname: {name}\ndescription: {description}\n---\n# Skill\n",
            )
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("(warning above 8000, the Codex skill-list budget)", completed.stdout)
        self.assertNotIn("warning above 350", completed.stdout)

    def test_kernel_skill_table_names_missing_skills(self) -> None:
        kernel = self.framework / "kernel" / "KERNEL.md"
        kernel.write_text(
            kernel.read_text()
            + "\n## 8. Skills\n\n| Situation | Skill |\n| --- | --- |\n"
            "| Demo work | `demo-skill` |\n| Updating `PROJECT_STATE.md` | `state-and-handoff` |\n"
            "| Planning | `change-planning` |\n\nMention of `not-in-table` outside the table.\n"
            "\n## 9. Communication\n\n| Other | `other-table-skill` |\n"
        )
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn(
            "skills table names skills without a skills/<name>/ directory: "
            "state-and-handoff, change-planning",
            completed.stdout,
        )
        self.assertNotIn("not-in-table", completed.stdout)
        self.assertNotIn("other-table-skill", completed.stdout)
        self.assertNotIn("PROJECT_STATE", completed.stdout)


if __name__ == "__main__":
    unittest.main()
