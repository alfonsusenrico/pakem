"""pakem lint --profile and pakem doctor --profile, each exercised against temporary profile directories."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from support import FIXTURE_REFERENCE, IsolatedTestCase, write

PROFILE_SKILL = textwrap.dedent(
    """\
    ---
    name: acme-reporting
    description: Use when building Acme reports.
    ---
    # Acme reporting

    See [the guide](references/guide.md).
    """
)

PROFILE_TOML = textwrap.dedent(
    """\
    name = "acme"
    version = "0.1.0"
    requires_framework = ">=0.2,<1.0"
    digest = "DIGEST.md"
    skills_dir = "skills"
    """
)

PROFILE_DIGEST = "# Acme profile\n\nUse the institutional data dictionary for column names.\n"

PREFIX = "[profile acme] "


class ProfileLintTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.profile = self.build_profile(self.base / "profile-acme")

    def build_profile(self, root: Path, toml_text: str = PROFILE_TOML) -> Path:
        write(root / "profile.toml", toml_text)
        write(root / "VERSION", "0.1.0\n")
        write(root / "DIGEST.md", PROFILE_DIGEST)
        write(root / "README.md", "# Acme profile\n\nSee [the digest](DIGEST.md).\n")
        write(root / "skills" / "acme-reporting" / "SKILL.md", PROFILE_SKILL)
        write(root / "skills" / "acme-reporting" / "references" / "guide.md", FIXTURE_REFERENCE)
        return root

    def lint(self, *profiles: Path):
        arguments = ["lint"]
        for profile in profiles or (self.profile,):
            arguments += ["--profile", str(profile)]
        return self.pakem(*arguments)

    def assert_lint_error(self, needle: str) -> None:
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("ERROR: " + PREFIX + needle, completed.stdout)

    def assert_lint_warning(self, needle: str) -> None:
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("WARNING: " + PREFIX + needle, completed.stdout)

    def skill_file(self) -> Path:
        return self.profile / "skills" / "acme-reporting" / "SKILL.md"

    def reference_file(self) -> Path:
        return self.profile / "skills" / "acme-reporting" / "references" / "guide.md"


class ProfileContractTests(ProfileLintTestCase):
    def test_clean_profile_passes(self) -> None:
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("0 error(s), 0 warning(s)", completed.stdout)
        self.assertIn(f"profile {self.profile}", completed.stdout)

    def test_framework_findings_are_still_reported(self) -> None:
        (self.framework / "kernel" / "KERNEL.md").unlink()
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn("ERROR: kernel/KERNEL.md is missing", completed.stdout)

    def test_profile_load_errors_are_lint_errors(self) -> None:
        cases = [
            ("name = \n", "invalid TOML"),
            (PROFILE_TOML.replace('version = "0.1.0"\n', ""), "'version' must be a non-empty string"),
            (PROFILE_TOML.replace(">=0.2,<1.0", ">=9.0"), "requires framework >=9.0"),
            (PROFILE_TOML.replace('"DIGEST.md"', '"MISSING.md"'), "digest file not found: MISSING.md"),
            (PROFILE_TOML.replace('"skills"', '"../elsewhere"'), "skills_dir escapes the profile directory"),
        ]
        for toml_text, needle in cases:
            with self.subTest(needle=needle):
                (self.profile / "profile.toml").write_text(toml_text)
                completed = self.lint()
                self.assert_fails(completed)
                self.assertIn(f"ERROR: [profile {self.profile}] ", completed.stdout)
                self.assertIn(needle, completed.stdout)
                self.assertEqual(completed.stderr, "")

    def test_missing_profile_directory_is_a_lint_error(self) -> None:
        missing = self.base / "no-such-profile"
        completed = self.lint(missing)
        self.assert_fails(completed)
        self.assertIn(f"ERROR: [profile {missing}] missing profile.toml", completed.stdout)

    def test_version_file_must_match_profile_toml(self) -> None:
        (self.profile / "VERSION").write_text("0.2.0\n")
        self.assert_lint_error("VERSION is '0.2.0' but profile.toml version is '0.1.0'")
        (self.profile / "VERSION").unlink()
        self.assert_ok(self.lint())

    def test_profile_without_skills_dir(self) -> None:
        (self.profile / "profile.toml").write_text(PROFILE_TOML.replace('skills_dir = "skills"\n', ""))
        write(self.profile / "skills" / "acme-reporting" / "SKILL.md", "# Not a profile skill any more\n")
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("0 error(s), 0 warning(s)", completed.stdout)


class ProfileDigestTests(ProfileLintTestCase):
    def test_digest_size_thresholds(self) -> None:
        digest = self.profile / "DIGEST.md"
        digest.write_text("# Acme\n\n" + "x" * 1_850 + "\n")
        self.assert_lint_warning("DIGEST.md is 1859 chars (warning above 1800)")
        digest.write_text("# Acme\n\n" + "x" * 2_050 + "\n")
        self.assert_lint_error("DIGEST.md is 2059 chars (limit 2000)")

    def test_digest_lines_repeating_the_kernel_warn(self) -> None:
        digest = self.profile / "DIGEST.md"
        digest.write_text(PROFILE_DIGEST + "\n---\n  Never print secret values.\n")
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn(
            "WARNING: " + PREFIX + "DIGEST.md:6: repeats a kernel/KERNEL.md line",
            completed.stdout,
        )
        self.assertNotIn("DIGEST.md:5", completed.stdout)
        self.assertIn("0 error(s), 1 warning(s)", completed.stdout)


class ProfileSkillTests(ProfileLintTestCase):
    def test_frontmatter_errors(self) -> None:
        self.skill_file().write_text(
            "---\nname: acme-reporting\ndescription: ok\ntags: [a]\n---\n# Body\n"
        )
        self.assert_lint_error("skills/acme-reporting/SKILL.md: frontmatter line 4: unsupported key 'tags'")
        self.skill_file().write_text("# No frontmatter\n")
        self.assert_lint_error("skills/acme-reporting/SKILL.md: first line must be '---'")

    def test_name_rules(self) -> None:
        self.skill_file().write_text(PROFILE_SKILL.replace("name: acme-reporting", "name: acme-other"))
        self.assert_lint_error("skills/acme-reporting/SKILL.md: name 'acme-other' does not equal directory name")
        self.skill_file().write_text(PROFILE_SKILL.replace("name: acme-reporting", "name: ACME_Reporting"))
        self.assert_lint_error("skills/acme-reporting/SKILL.md: name 'ACME_Reporting' must match")

    def test_description_limits(self) -> None:
        self.skill_file().write_text(PROFILE_SKILL.replace("Use when building Acme reports.", "d" * 400))
        self.assert_lint_warning("skills/acme-reporting/SKILL.md: description is 400 chars (warning above 350)")
        self.skill_file().write_text(PROFILE_SKILL.replace("Use when building Acme reports.", "d" * 1_100))
        self.assert_lint_error("skills/acme-reporting/SKILL.md: description is 1100 chars (limit 1024)")

    def test_body_limits(self) -> None:
        self.skill_file().write_text(PROFILE_SKILL + "line\n" * 510)
        self.assert_lint_error("skills/acme-reporting/SKILL.md: body is 513 lines (limit 500)")
        self.skill_file().write_text(PROFILE_SKILL + ("y" * 200 + "\n") * 110)
        self.assert_lint_error("skills/acme-reporting/SKILL.md: body is 22166 chars (limit 20000)")

    def test_skill_links_must_stay_inside_skill(self) -> None:
        self.skill_file().write_text(
            PROFILE_SKILL + "\n[out](../../README.md)\n[gone](references/missing.md)\n"
        )
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn(
            PREFIX + "skills/acme-reporting/SKILL.md:9: link '../../README.md' leaves the skill directory",
            completed.stdout,
        )
        self.assertIn(
            PREFIX + "skills/acme-reporting/SKILL.md:10: link 'references/missing.md' does not resolve",
            completed.stdout,
        )

    def test_reference_to_reference_warns(self) -> None:
        write(self.reference_file().parent / "second.md", FIXTURE_REFERENCE)
        self.reference_file().write_text(FIXTURE_REFERENCE + "\nSee [second](second.md).\n")
        self.assert_lint_warning("skills/acme-reporting/references/guide.md:9: reference links to another reference")

    def test_status_headers(self) -> None:
        self.reference_file().write_text("# Guide\n\nNo headers.\n")
        self.assert_lint_error("skills/acme-reporting/references/guide.md: missing 'Status:' line")
        self.reference_file().write_text(FIXTURE_REFERENCE.replace("2999-01-01", "2020-01-01"))
        self.assert_lint_error(
            "skills/acme-reporting/references/guide.md: review overdue (Review by: 2020-01-01)"
        )

    def test_skill_name_must_not_shadow_framework_skill(self) -> None:
        write(
            self.profile / "skills" / "demo-skill" / "SKILL.md",
            "---\nname: demo-skill\ndescription: Use when shadowing.\n---\n# Shadow\n",
        )
        self.assert_lint_error("skills/demo-skill: skill name 'demo-skill' is also a framework skill")

    def test_total_description_budget_includes_profiles(self) -> None:
        description = "Use when " + "d" * 330 + "."
        for index in range(12):
            name = f"framework-budget-{index}"
            write(
                self.framework / "skills" / name / "SKILL.md",
                f"---\nname: {name}\ndescription: {description}\n---\n# Skill\n",
            )
        framework_only = self.pakem("lint")
        self.assert_ok(framework_only)
        self.assertNotIn("descriptions total", framework_only.stdout)
        for index in range(12):
            name = f"acme-budget-{index}"
            write(
                self.profile / "skills" / name / "SKILL.md",
                f"---\nname: {name}\ndescription: {description}\n---\n# Skill\n",
            )
        completed = self.lint()
        self.assert_ok(completed)
        self.assertIn("WARNING: framework and profile skill descriptions total", completed.stdout)
        self.assertIn("(warning above 8000, the Codex skill-list budget)", completed.stdout)

    def test_skill_names_must_be_unique_across_profiles(self) -> None:
        other = self.build_profile(self.base / "profile-other", PROFILE_TOML.replace('"acme"', '"other"'))
        completed = self.lint(self.profile, other)
        self.assert_fails(completed)
        self.assertIn(
            "ERROR: skill name 'acme-reporting' is provided by both profile acme and profile other",
            completed.stdout,
        )


class ProfileFileTests(ProfileLintTestCase):
    def test_banned_patterns(self) -> None:
        home_like = "/" + "Users/someone/"
        write(self.profile / "docs" / "notes.md", f"path {home_like}x\nthe Caveman skill\n")
        write(self.profile / "ci" / "deploy.yml", "run: cut -d= -f1 .env\n")
        write(self.profile / "ci" / "deploy.yaml", "run: cut -d= -f1 .env\n")
        write(self.profile / "docs" / "adr" / "0001.md", "We retired caveman.\n")
        write(self.profile / "tests" / "fixture.md", f"{home_like}allowed\n")
        write(self.profile / "archive" / "old.md", "caveman\n")
        write(self.profile / "notes.txt", "caveman\n")
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn(PREFIX + "docs/notes.md:1: [absolute-home-path]", completed.stdout)
        self.assertIn(PREFIX + "docs/notes.md:2: [retired-skill-names]", completed.stdout)
        self.assertIn(PREFIX + "ci/deploy.yml:1: [cut-env-values]", completed.stdout)
        self.assertIn(PREFIX + "ci/deploy.yaml:1: [cut-env-values]", completed.stdout)
        self.assertNotIn("docs/adr", completed.stdout)
        self.assertNotIn("tests/fixture.md", completed.stdout)
        self.assertNotIn("archive/", completed.stdout)
        self.assertNotIn("notes.txt", completed.stdout)
        self.assertIn("4 error(s)", completed.stdout)

    def test_markdown_links(self) -> None:
        write(
            self.profile / "docs" / "guide.md",
            "[ok](../DIGEST.md) [web](https://example.com) [anchor](#top)\n"
            "`[code](nope.md)`\n[broken](missing.md#part)\n",
        )
        write(self.profile / "archive" / "old.md", "[broken](nowhere.md)\n")
        completed = self.lint()
        self.assert_fails(completed)
        self.assertIn(PREFIX + "docs/guide.md:3: broken link 'missing.md#part'", completed.stdout)
        self.assertNotIn("nope.md", completed.stdout)
        self.assertNotIn("archive/old.md", completed.stdout)
        self.assertIn("1 error(s)", completed.stdout)


class DoctorProfileTests(ProfileLintTestCase):
    INSTALL = ("install", "--allow-dirty")

    def test_installed_profile_skills_pass(self) -> None:
        self.assert_ok(self.pakem(*self.INSTALL, "--profile", str(self.profile), "--apply"))
        completed = self.pakem("doctor", "--profile", str(self.profile))
        self.assert_ok(completed)
        self.assertIn("0 error(s), 0 warning(s)", completed.stdout)

    def test_missing_and_outdated_profile_skills(self) -> None:
        self.assert_ok(self.pakem(*self.INSTALL, "--apply"))
        completed = self.pakem("doctor", "--profile", str(self.profile))
        self.assert_fails(completed)
        self.assertIn(
            "ERROR: not installed: profile:acme/acme-reporting at ~/.claude/skills/acme-reporting", completed.stdout
        )

        self.assert_ok(self.pakem(*self.INSTALL, "--profile", str(self.profile), "--apply"))
        self.skill_file().write_text(PROFILE_SKILL + "\nMore guidance.\n")
        completed = self.pakem("doctor", "--profile", str(self.profile))
        self.assert_ok(completed)
        self.assertIn(
            "WARNING: outdated: ~/.claude/skills/acme-reporting differs from profile:acme/acme-reporting",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
