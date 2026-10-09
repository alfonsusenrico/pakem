"""af project sync / check and tools/check_pinned_blocks.sh."""

from __future__ import annotations

import subprocess
import unittest

from support import CHECK_SCRIPT, IsolatedTestCase, git, init_repo, isolated_env, write

LEGACY_TEXT = "# App\n\n<!-- owner-baseline:start -->\nold baseline\n<!-- owner-baseline:end -->\n\nNotes.\n"


class ProjectSyncTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = init_repo(self.base / "app", self.home)

    def agents(self) -> str:
        return (self.repo / "AGENTS.md").read_text()

    def make_profile(self, version: str = "1.0.0", digest: str = "Profile rule one.\n"):
        profile = self.base / "profile"
        write(
            profile / "profile.toml",
            f'name = "work"\nversion = "{version}"\nrequires_framework = ">=0.1,<2.0"\ndigest = "DIGEST.md"\n',
        )
        write(profile / "DIGEST.md", digest)
        return profile

    def test_dry_run_prints_diff_and_writes_nothing(self) -> None:
        completed = self.af("project", "sync", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("+<!-- agent-framework:digest version=0.2.0 sha256=", completed.stdout)
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_creates_from_template_and_is_idempotent(self) -> None:
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        text = self.agents()
        lines = text.splitlines()
        self.assertEqual(lines[0], "# app")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].startswith("<!-- agent-framework:digest version=0.2.0 sha256="))
        self.assertIn("Project notes.", text)

        again = self.af("project", "sync", str(self.repo), "--apply")
        self.assert_ok(again)
        self.assertIn("up to date", again.stdout)
        self.assertEqual(text, self.agents())
        self.assert_ok(self.af("project", "check", str(self.repo)))

    def test_inserts_after_h1_in_existing_file(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nKeep this paragraph.\n")
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        lines = self.agents().splitlines()
        self.assertEqual(lines[0], "# Existing")
        self.assertTrue(lines[2].startswith("<!-- agent-framework:digest"))
        self.assertEqual(lines[-1], "Keep this paragraph.")

    def test_refreshes_block_in_place_when_digest_changes(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nCapabilities: database\n")
        (self.repo / "research").mkdir()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        write(self.framework / "VERSION", "0.3.0\n")
        digest = self.framework / "kernel" / "DIGEST.md"
        digest.write_text(digest.read_text() + "Always verify before claiming success.\n")
        stale = self.af("project", "check", str(self.repo))
        self.assert_ok(stale)
        self.assertIn("WARNING: digest block (version 0.2.0) differs from the framework 0.3.0", stale.stdout)

        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        text = self.agents()
        self.assertEqual(text.count("<!-- agent-framework:digest"), 1)
        self.assertIn("version=0.3.0", text)
        check = self.af("project", "check", str(self.repo))
        self.assert_ok(check)
        self.assertIn("0 error(s), 0 warning(s)", check.stdout)

    def test_older_version_with_unchanged_digest_is_not_a_warning(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nCapabilities: database\n")
        (self.repo / "research").mkdir()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        write(self.framework / "VERSION", "0.3.0\n")
        check = self.af("project", "check", str(self.repo))
        self.assert_ok(check)
        self.assertIn("0 error(s), 0 warning(s)", check.stdout)
        self.assertNotIn("digest block", check.stdout)

    def test_same_version_with_changed_digest_warns(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nCapabilities: database\n")
        (self.repo / "research").mkdir()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        digest = self.framework / "kernel" / "DIGEST.md"
        digest.write_text(digest.read_text() + "Always verify before claiming success.\n")
        check = self.af("project", "check", str(self.repo))
        self.assert_ok(check)
        self.assertIn("WARNING: digest block (version 0.2.0) differs from the framework 0.2.0", check.stdout)
        self.assertIn("0 error(s), 1 warning(s)", check.stdout)

    def test_invalid_digest_version_is_an_error(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nCapabilities: database\n")
        (self.repo / "research").mkdir()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        text = self.agents().replace("digest version=0.2.0 ", "digest version=next ")
        write(self.repo / "AGENTS.md", text)
        check = self.af("project", "check", str(self.repo))
        self.assert_fails(check)
        self.assertIn("digest block has an invalid version: 'next'", check.stdout)

    def test_profile_block_follows_digest(self) -> None:
        profile = self.make_profile()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        text = self.agents()
        digest_at = text.index("<!-- agent-framework:digest")
        profile_at = text.index("<!-- agent-framework:profile name=work version=1.0.0 sha256=")
        self.assertLess(digest_at, profile_at)
        self.assertIn("Profile rule one.", text)

        self.make_profile(version="1.1.0", digest="Profile rule two.\n")
        self.assert_ok(self.af("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        text = self.agents()
        self.assertEqual(text.count("<!-- agent-framework:profile"), 1)
        self.assertIn("Profile rule two.", text)
        self.assertNotIn("Profile rule one.", text)
        self.assert_ok(self.af("project", "check", str(self.repo)))

    def test_profile_framework_requirement_is_enforced(self) -> None:
        profile = self.make_profile()
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=1.0"\ndigest = "DIGEST.md"\n',
        )
        completed = self.af("project", "sync", str(self.repo), "--profile", str(profile))
        self.assert_fails(completed, code=2)
        self.assertIn("requires framework >=1.0", completed.stderr)

    def test_legacy_block_reported_but_kept(self) -> None:
        write(self.repo / "AGENTS.md", LEGACY_TEXT)
        completed = self.af("project", "sync", str(self.repo), "--apply")
        self.assert_ok(completed)
        self.assertIn("legacy owner-baseline block", completed.stdout)
        text = self.agents()
        self.assertIn("old baseline", text)
        self.assertIn("<!-- agent-framework:digest", text)

    def test_legacy_block_replaced_on_request(self) -> None:
        write(self.repo / "AGENTS.md", LEGACY_TEXT)
        self.assert_ok(self.af("project", "sync", str(self.repo), "--replace-legacy", "--apply"))
        text = self.agents()
        self.assertNotIn("owner-baseline", text)
        self.assertNotIn("old baseline", text)
        self.assertEqual(text.count("<!-- agent-framework:digest"), 1)
        self.assertTrue(text.endswith("Notes.\n"))
        self.assert_ok(self.af("project", "check", str(self.repo)))


class ProjectCheckTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = init_repo(self.base / "app", self.home)
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        self.agents_path = self.repo / "AGENTS.md"

    def tamper(self) -> None:
        text = self.agents_path.read_text()
        self.agents_path.write_text(text.replace("Never print secret values.", "Print secret values freely."))

    def run_script(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/sh", str(CHECK_SCRIPT), str(self.repo)],
            capture_output=True,
            text=True,
            env=isolated_env(self.home),
        )

    def test_detects_tampered_block(self) -> None:
        self.tamper()
        completed = self.af("project", "check", str(self.repo))
        self.assert_fails(completed)
        self.assertIn("does not match its sha256", completed.stdout)

    def test_shell_script_agrees_on_good_and_tampered(self) -> None:
        self.assert_ok(self.run_script())
        self.assert_ok(self.af("project", "check", str(self.repo)))
        self.tamper()
        script = self.run_script()
        self.assert_fails(script)
        self.assertIn("does not match its sha256", script.stdout)
        self.assert_fails(self.af("project", "check", str(self.repo)))

    def test_shell_script_agrees_with_profile_and_crlf(self) -> None:
        profile = self.base / "profile"
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1"\ndigest = "D.md"\n',
        )
        write(profile / "D.md", "Rule with  double  spaces.\n\nSecond paragraph.\n")
        self.assert_ok(self.af("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        crlf = self.agents_path.read_text().replace("\n", "\r\n")
        self.agents_path.write_bytes(crlf.encode())
        self.assert_ok(self.run_script())
        self.assert_ok(self.af("project", "check", str(self.repo)))

    def test_size_limits(self) -> None:
        text = self.agents_path.read_text()
        self.agents_path.write_text(text + "x" * 10_500 + "\n")
        warned = self.af("project", "check", str(self.repo))
        self.assert_ok(warned)
        self.assertIn("warning above 10000", warned.stdout)
        self.agents_path.write_text(text + "x" * 12_500 + "\n")
        self.assert_fails(self.af("project", "check", str(self.repo)))
        self.assert_fails(self.run_script())

    def test_overrides_validation(self) -> None:
        text = self.agents_path.read_text()
        valid = (
            text
            + "\n## Overrides\n\n- `default:branching` This repo commits to trunk.\n  continuation line\n"
        )
        self.agents_path.write_text(valid)
        self.assert_ok(self.af("project", "check", str(self.repo)))

        invalid = text + "\n## Overrides\n\n- `default:unknown-label` Something.\n- No label here.\n"
        self.agents_path.write_text(invalid)
        completed = self.af("project", "check", str(self.repo))
        self.assert_fails(completed)
        self.assertIn("unknown override label 'default:unknown-label'", completed.stdout)
        self.assertIn("must start with `default:<label>`", completed.stdout)

    def test_home_paths_in_tracked_docs_warn(self) -> None:
        write(self.repo / "CLAUDE.md", "See ~/notes and /Users/someone/x\n")
        git(self.repo, "add", "CLAUDE.md", "AGENTS.md", home=self.home)
        completed = self.af("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("CLAUDE.md line 1: machine-specific home path", completed.stdout)

    def test_missing_agents_file_is_error(self) -> None:
        self.agents_path.unlink()
        self.assert_fails(self.af("project", "check", str(self.repo)))


class ProjectCapabilityAndFileTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = init_repo(self.base / "app", self.home)
        (self.repo / "research").mkdir()
        self.assert_ok(self.af("project", "sync", str(self.repo), "--apply"))
        self.agents_path = self.repo / "AGENTS.md"
        self.synced = self.agents_path.read_text().replace("Capabilities: none\n", "")

    def check_with(self, capability_lines: str) -> subprocess.CompletedProcess:
        self.agents_path.write_text(self.synced + capability_lines)
        return self.af("project", "check", str(self.repo))

    def test_known_capabilities_pass_cleanly(self) -> None:
        for line in ("Capabilities: database, deployed\n", "- Capabilities: `database`, `deployed`\n"):
            with self.subTest(line=line):
                completed = self.check_with(line)
                self.assert_ok(completed)
                self.assertIn("0 error(s), 0 warning(s)", completed.stdout)

    def test_none_declares_no_capabilities(self) -> None:
        completed = self.check_with("Capabilities: none\n")
        self.assert_ok(completed)
        self.assertIn("0 error(s), 0 warning(s)", completed.stdout)

    def test_missing_line_warns(self) -> None:
        completed = self.check_with("")
        self.assert_ok(completed)
        self.assertIn("declare project capabilities", completed.stdout)

    def test_key_is_case_sensitive(self) -> None:
        completed = self.check_with("capabilities: database\n")
        self.assert_ok(completed)
        self.assertIn("declare project capabilities", completed.stdout)

    def test_invalid_values_are_errors(self) -> None:
        cases = [
            (
                "Capabilities: database, telepathy\n",
                "unknown capability 'telepathy' (known: database, deployed)",
            ),
            ("Capabilities: always\n", "'always' applies to every project"),
            ("Capabilities: none, database\n", "'none' cannot be combined"),
            ("Capabilities:\n", "has no values"),
            ("Capabilities: database\nCapabilities: deployed\n", "more than one line"),
        ]
        for line, needle in cases:
            with self.subTest(line=line):
                completed = self.check_with(line)
                self.assert_fails(completed)
                self.assertIn(needle, completed.stdout)

    def test_duplicate_capability_warns(self) -> None:
        completed = self.check_with("Capabilities: database, database\n")
        self.assert_ok(completed)
        self.assertIn("capability 'database' is listed twice", completed.stdout)

    def test_line_inside_pinned_block_is_ignored(self) -> None:
        profile = self.base / "profile"
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1"\ndigest = "D.md"\n',
        )
        write(profile / "D.md", "Capabilities: telepathy\n")
        self.agents_path.write_text(self.synced)
        self.assert_ok(self.af("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        completed = self.af("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertNotIn("telepathy", completed.stdout)
        self.assertIn("declare project capabilities", completed.stdout)

    def test_unreadable_registry_is_an_error(self) -> None:
        registry = self.framework / "standards" / "capabilities.toml"
        registry.write_text("[capabilities.database]\nsummary = \"x\"\n")
        completed = self.check_with("Capabilities: database\n")
        self.assert_fails(completed)
        self.assertIn("cannot check declared capabilities", completed.stdout)

    def test_project_state_size_limits(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        state = self.repo / "PROJECT_STATE.md"
        state.write_text("# State\n" + "x" * 7_900 + "\n")
        self.assertIn("0 error(s), 0 warning(s)", self.af("project", "check", str(self.repo)).stdout)
        state.write_text("# State\n" + "x" * 8_100 + "\n")
        warned = self.af("project", "check", str(self.repo))
        self.assert_ok(warned)
        self.assertIn("PROJECT_STATE.md is 8109 chars (warning above 8000)", warned.stdout)
        state.write_text("# State\n" + "x" * 16_100 + "\n")
        failed = self.af("project", "check", str(self.repo))
        self.assert_fails(failed)
        self.assertIn("PROJECT_STATE.md is 16109 chars (limit 16000)", failed.stdout)

    def test_missing_research_folder_warns(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        (self.repo / "research").rmdir()
        completed = self.af("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("research/ folder is missing", completed.stdout)


if __name__ == "__main__":
    unittest.main()
