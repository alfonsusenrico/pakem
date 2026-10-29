"""pakem project sync / check and tools/check_pinned_blocks.sh."""

from __future__ import annotations

import subprocess
import unittest

from support import CHECK_SCRIPT, IsolatedTestCase, activate_hooks, context_profile, git, init_repo, isolated_env, write

LEGACY_TEXT = "# App\n\n<!-- owner-baseline:start -->\nold baseline\n<!-- owner-baseline:end -->\n\nNotes.\n"


class ProjectSyncTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = activate_hooks(init_repo(self.base / "app", self.home), self.home)

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
        completed = self.pakem("project", "sync", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("+# app", completed.stdout)
        self.assertNotIn(":digest", completed.stdout)
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_creates_from_template_without_framework_block_and_is_idempotent(self) -> None:
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--apply"))
        text = self.agents()
        self.assertEqual(text.splitlines()[0], "# app")
        self.assertIn("Project notes.", text)
        self.assertNotIn("pakem:", text)

        again = self.pakem("project", "sync", str(self.repo), "--apply")
        self.assert_ok(again)
        self.assertIn("up to date", again.stdout)
        self.assertEqual(text, self.agents())
        self.assert_ok(self.pakem("project", "check", str(self.repo)))

    def test_obsolete_digest_block_is_reported_and_removed(self) -> None:
        write(
            self.repo / "AGENTS.md",
            "# Existing\n\n<!-- agent-framework:digest version=1.1.1 sha256=0000 -->\nOld kernel lines.\n"
            "<!-- /agent-framework:digest -->\n\nCapabilities: database\nDelivery: us\n",
        )
        (self.repo / "research").mkdir()
        check = self.pakem("project", "check", str(self.repo))
        self.assert_ok(check)
        self.assertIn("obsolete framework digest block at line 3", check.stdout)

        synced = self.pakem("project", "sync", str(self.repo), "--apply")
        self.assert_ok(synced)
        self.assertIn("removed the framework digest block", synced.stdout)
        self.assertEqual(self.agents(), "# Existing\n\nCapabilities: database\nDelivery: us\n")
        clean = self.pakem("project", "check", str(self.repo))
        self.assertIn("AGENTS.md pins no profile", clean.stdout)
        self.assertIn("0 error(s), 1 warning(s)", clean.stdout)

    def test_profile_block_inserted_after_h1_and_refreshed(self) -> None:
        write(self.repo / "AGENTS.md", "# Existing\n\nKeep this paragraph.\n")
        profile = self.make_profile()
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        lines = self.agents().splitlines()
        self.assertEqual(lines[0], "# Existing")
        self.assertTrue(lines[2].startswith("<!-- pakem:profile name=work version=1.0.0 sha256="))
        self.assertEqual(lines[-1], "Keep this paragraph.")
        self.assertIn("Profile rule one.", self.agents())

        self.make_profile(version="1.1.0", digest="Profile rule two.\n")
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        text = self.agents()
        self.assertEqual(text.count("<!-- pakem:profile"), 1)
        self.assertIn("Profile rule two.", text)
        self.assertNotIn("Profile rule one.", text)
        self.assert_ok(self.pakem("project", "check", str(self.repo)))

    def test_profile_framework_requirement_is_enforced(self) -> None:
        profile = self.make_profile()
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=1.0"\ndigest = "DIGEST.md"\n',
        )
        completed = self.pakem("project", "sync", str(self.repo), "--profile", str(profile))
        self.assert_fails(completed, code=2)
        self.assertIn("requires framework >=1.0", completed.stderr)

    def test_legacy_block_reported_but_kept(self) -> None:
        write(self.repo / "AGENTS.md", LEGACY_TEXT)
        completed = self.pakem("project", "sync", str(self.repo), "--apply")
        self.assert_ok(completed)
        self.assertIn("pass --replace-legacy to remove it", completed.stdout)
        self.assertIn("old baseline", self.agents())

    def test_legacy_block_removed_on_request(self) -> None:
        write(self.repo / "AGENTS.md", LEGACY_TEXT)
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--replace-legacy", "--apply"))
        text = self.agents()
        self.assertNotIn("owner-baseline", text)
        self.assertNotIn("old baseline", text)
        self.assertNotIn("pakem:", text)
        self.assertTrue(text.endswith("Notes.\n"))
        self.assert_ok(self.pakem("project", "check", str(self.repo)))


class ProjectCheckTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = activate_hooks(init_repo(self.base / "app", self.home), self.home)
        base_profile = self.base / "base-profile"
        write(
            base_profile / "profile.toml",
            'name = "base"\nversion = "1.0.0"\nrequires_framework = ">=0.1"\ndigest = "D.md"\n',
        )
        write(base_profile / "D.md", "Never print secret values.\n")
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(base_profile), "--apply"))
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
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_fails(completed)
        self.assertIn("does not match its sha256", completed.stdout)

    def test_shell_script_agrees_on_good_and_tampered(self) -> None:
        self.assert_ok(self.run_script())
        self.assert_ok(self.pakem("project", "check", str(self.repo)))
        self.tamper()
        script = self.run_script()
        self.assert_fails(script)
        self.assertIn("does not match its sha256", script.stdout)
        self.assert_fails(self.pakem("project", "check", str(self.repo)))

    def test_former_marker_name_is_read_reported_and_rewritten(self) -> None:
        current = self.agents_path.read_text()
        former = current.replace("<!-- pakem:profile", "<!-- agent-framework:profile").replace(
            "<!-- /pakem:profile", "<!-- /agent-framework:profile"
        )
        self.assertNotEqual(current, former)
        self.agents_path.write_text(former)

        self.assert_ok(self.run_script())
        checked = self.pakem("project", "check", str(self.repo))
        self.assert_ok(checked)
        self.assertIn("uses the former marker name agent-framework", checked.stdout)

        profile = self.base / "base-profile"
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        self.assertEqual(self.agents_path.read_text(), current)
        self.assertNotIn("former marker name", self.pakem("project", "check", str(self.repo)).stdout)

    def test_shell_script_agrees_with_profile_and_crlf(self) -> None:
        profile = self.base / "profile"
        write(
            profile / "profile.toml",
            'name = "work"\nversion = "1.0.0"\nrequires_framework = ">=0.1"\ndigest = "D.md"\n',
        )
        write(profile / "D.md", "Rule with  double  spaces.\n\nSecond paragraph.\n")
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        crlf = self.agents_path.read_text().replace("\n", "\r\n")
        self.agents_path.write_bytes(crlf.encode())
        self.assert_ok(self.run_script())
        self.assert_ok(self.pakem("project", "check", str(self.repo)))

    def test_size_limits(self) -> None:
        text = self.agents_path.read_text()
        self.agents_path.write_text(text + "x" * 10_500 + "\n")
        warned = self.pakem("project", "check", str(self.repo))
        self.assert_ok(warned)
        self.assertIn("warning above 10000", warned.stdout)
        self.agents_path.write_text(text + "x" * 12_500 + "\n")
        self.assert_fails(self.pakem("project", "check", str(self.repo)))
        self.assert_fails(self.run_script())

    def test_overrides_validation(self) -> None:
        text = self.agents_path.read_text()
        valid = (
            text
            + "\n## Overrides\n\n- `default:branching` This repo commits to trunk.\n  continuation line\n"
        )
        self.agents_path.write_text(valid)
        self.assert_ok(self.pakem("project", "check", str(self.repo)))

        invalid = text + "\n## Overrides\n\n- `default:unknown-label` Something.\n- No label here.\n"
        self.agents_path.write_text(invalid)
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_fails(completed)
        self.assertIn("unknown override label 'default:unknown-label'", completed.stdout)
        self.assertIn("must start with `default:<label>`", completed.stdout)

    def test_home_paths_in_tracked_docs_warn(self) -> None:
        write(self.repo / "CLAUDE.md", "See ~/notes and /Users/someone/x\n")
        git(self.repo, "add", "CLAUDE.md", "AGENTS.md", home=self.home)
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("CLAUDE.md line 1: machine-specific home path", completed.stdout)

    def test_missing_agents_file_is_error(self) -> None:
        self.agents_path.unlink()
        self.assert_fails(self.pakem("project", "check", str(self.repo)))


class ProjectCapabilityAndFileTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = activate_hooks(init_repo(self.base / "app", self.home), self.home)
        (self.repo / "research").mkdir()
        profile = context_profile(self.base / "context-profile")
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        self.agents_path = self.repo / "AGENTS.md"
        self.synced = self.agents_path.read_text().replace("Capabilities: none\n", "")

    def check_with(self, capability_lines: str) -> subprocess.CompletedProcess:
        self.agents_path.write_text(self.synced + capability_lines)
        return self.pakem("project", "check", str(self.repo))

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
        self.assertIn("Declare project capabilities", completed.stdout)

    def test_key_is_case_sensitive(self) -> None:
        completed = self.check_with("capabilities: database\n")
        self.assert_ok(completed)
        self.assertIn("Declare project capabilities", completed.stdout)

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

    def test_delivery_line_is_checked(self) -> None:
        base = self.synced.replace("Delivery: nobody\n", "")
        cases = [
            ("Capabilities: database\n", "has no 'Delivery:' line", True),
            ("Capabilities: database\nDelivery: maybe\n", "should start with us, others, or nobody", True),
            ("Capabilities: database\nDelivery: us\nDelivery: nobody\n", "more than one line", False),
            ("Capabilities: database\nDelivery: others (the platform team)\n", "0 error(s), 0 warning(s)", True),
        ]
        for lines, needle, passes in cases:
            with self.subTest(lines=lines):
                self.agents_path.write_text(base + lines)
                completed = self.pakem("project", "check", str(self.repo))
                (self.assert_ok if passes else self.assert_fails)(completed)
                self.assertIn(needle, completed.stdout)

    def test_override_labels_come_from_kernel_and_pinned_profiles(self) -> None:
        overrides = "Capabilities: database\n\n## Overrides\n\n"
        profile_label = self.check_with(overrides + "- `default:free-first`: a paid model, USD 2 a month.\n")
        self.assert_ok(profile_label)
        self.assertIn("0 error(s), 0 warning(s)", profile_label.stdout)
        unknown = self.check_with(overrides + "- `default:made-up`: no such default.\n")
        self.assert_fails(unknown)
        self.assertIn("not defined in kernel/KERNEL.md or a pinned profile", unknown.stdout)

    def test_retired_label_warns_instead_of_failing(self) -> None:
        completed = self.check_with("Capabilities: database\n\n## Overrides\n\n- `default:no-billed`: paid replies.\n")
        self.assert_ok(completed)
        self.assertIn("`default:no-billed` is retired (ADR 0012)", completed.stdout)
        self.assertIn("0 error(s), 1 warning(s)", completed.stdout)

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
        self.assert_ok(self.pakem("project", "sync", str(self.repo), "--profile", str(profile), "--apply"))
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertNotIn("telepathy", completed.stdout)
        self.assertIn("Declare project capabilities", completed.stdout)

    def test_unreadable_registry_is_an_error(self) -> None:
        registry = self.framework / "standards" / "capabilities.toml"
        registry.write_text("[capabilities.database]\nsummary = \"x\"\n")
        completed = self.check_with("Capabilities: database\n")
        self.assert_fails(completed)
        self.assertIn("cannot check declared capabilities", completed.stdout)

    def test_settings_without_descriptions_warn(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        write(self.repo / "deploy" / "settings.toml", 'af_standard = 1\nplacement = ["PUBLIC_HOST"]\n'
              'secrets = ["API_TOKEN", "MAIL_PASSWORD"]\n\n[descriptions]\nAPI_TOKEN = "From the dashboard"\n')
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("deploy/settings.toml has no [descriptions] entry for PUBLIC_HOST, MAIL_PASSWORD", completed.stdout)
        self.assertIn("0 error(s), 1 warning(s)", completed.stdout)

    def test_project_state_size_limits(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        state = self.repo / "PROJECT_STATE.md"
        state.write_text("# State\n" + "x" * 7_900 + "\n")
        self.assertIn("0 error(s), 0 warning(s)", self.pakem("project", "check", str(self.repo)).stdout)
        state.write_text("# State\n" + "x" * 8_100 + "\n")
        warned = self.pakem("project", "check", str(self.repo))
        self.assert_ok(warned)
        self.assertIn("PROJECT_STATE.md is 8109 chars (warning above 8000)", warned.stdout)
        state.write_text("# State\n" + "x" * 16_100 + "\n")
        failed = self.pakem("project", "check", str(self.repo))
        self.assert_fails(failed)
        self.assertIn("PROJECT_STATE.md is 16109 chars (limit 16000)", failed.stdout)

    def test_missing_research_folder_warns(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        (self.repo / "research").rmdir()
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("research/ folder is missing", completed.stdout)

    def test_inactive_hooks_warn(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        git(self.repo, "config", "--unset", "core.hooksPath", home=self.home)
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        self.assertIn("framework git hooks are not active here (core.hooksPath is unset)", completed.stdout)

    def test_an_unfiltered_prune_in_a_pipeline_warns(self) -> None:
        self.agents_path.write_text(self.synced + "Capabilities: none\n")
        write(self.repo / ".gitlab-ci.yml", (
            "deploy:\n  script:\n"
            "    # never docker system prune here\n"
            "    - docker image prune -f\n"
            "    - docker image prune -f --filter until=24h\n"
            "    - docker image prune -f --filter label=project=app\n"
            "    - docker image rm app:old\n"
        ))
        write(self.repo / "deploy" / "clean.sh", "docker system prune \\\n  --all --force\n")
        write(self.repo / "docs" / "ops.md", "Never run docker image prune on the server.\n")
        completed = self.pakem("project", "check", str(self.repo))
        self.assert_ok(completed)
        warnings = [line for line in completed.stdout.splitlines() if "without a label filter" in line]
        self.assertEqual(len(warnings), 3, completed.stdout)
        for place in (".gitlab-ci.yml line 4: `docker image prune`", ".gitlab-ci.yml line 5:",
                      "deploy/clean.sh line 1: `docker system prune`"):
            self.assertIn(place, completed.stdout)


if __name__ == "__main__":
    unittest.main()
