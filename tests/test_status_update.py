"""pakem status, update, config, and the session hooks, against a local origin."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from support import PAKEM, REPO_ROOT, IsolatedTestCase, git, init_repo, isolated_env, write

PROFILE_TOML = 'name = "work"\nversion = "{version}"\nrequires_framework = ">=0.1"\ndigest = "DIGEST.md"\n'


class StatusUpdateTestCase(IsolatedTestCase):
    def build_framework(self, root, **target_options):
        root = super().build_framework(root, **target_options)
        # `pakem update` runs the checkout's own tool, so the fixture carries a copy of it.
        shutil.copytree(REPO_ROOT / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO_ROOT / "enforcement", root / "enforcement")
        shutil.copy(REPO_ROOT / ".gitignore", root / ".gitignore")
        return root

    def setUp(self) -> None:
        super().setUp()
        init_repo(self.framework, self.home)
        self.commit(self.framework, "chore: fixture")
        self.origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(self.origin), home=self.home)
        git(self.framework, "remote", "add", "origin", str(self.origin), home=self.home)
        git(self.framework, "push", "-q", "origin", "main", home=self.home)
        git(self.framework, "fetch", "-q", "origin", home=self.home)

    def commit(self, repo, message: str) -> None:
        git(repo, "add", "-A", home=self.home)
        git(repo, "commit", "-q", "--allow-empty", "-m", message, home=self.home)

    def head(self, repo=None) -> str:
        return git(repo or self.framework, "rev-parse", "HEAD", home=self.home).stdout.strip()

    def manifest(self) -> dict:
        return json.loads((self.home / ".local" / "state" / "pakem" / "manifest.json").read_text())

    def push_upstream(self, kernel_line: str = "Upstream rule.", version: str | None = None, tag: str | None = None):
        """Merge a change on origin/main from another clone, as a merged pull request would."""
        clone = self.base / "upstream"
        if not clone.exists():
            git(self.base, "clone", "-q", str(self.origin), str(clone), home=self.home)
            git(clone, "config", "commit.gpgsign", "false", home=self.home)
        git(clone, "pull", "-q", "origin", "main", home=self.home)
        kernel = clone / "kernel" / "KERNEL.md"
        kernel.write_text(kernel.read_text() + kernel_line + "\n")
        skill = clone / "skills" / "demo-skill" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nMore guidance.\n")
        if version:
            (clone / "VERSION").write_text(version + "\n")
        self.commit(clone, "feat: upstream change")
        git(clone, "push", "-q", "origin", "main", home=self.home)
        if tag:
            git(clone, "tag", tag, home=self.home)
            git(clone, "push", "-q", "origin", tag, home=self.home)
        return self.head(clone)

    def status_json(self, *extra: str, cwd=None) -> dict:
        completed = self.pakem("status", "--json", *extra, cwd=cwd)
        self.assert_ok(completed)
        return json.loads(completed.stdout)

    def hook(self, kind: str, payload) -> subprocess.CompletedProcess:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "status", "--hook", kind, "--host", "claude"],
            input=raw,
            capture_output=True,
            text=True,
            env=isolated_env(self.home),
            cwd=str(self.base),
        )


class StatusTests(StatusUpdateTestCase):
    def test_install_records_what_update_repeats(self) -> None:
        self.assert_ok(self.pakem("install", "--apply", "--hosts", "claude"))
        manifest = self.manifest()
        self.assertEqual(manifest["framework_commit"], self.head())
        self.assertTrue(manifest["framework_clean"])
        self.assertEqual(manifest["install_options"], {"hosts": "claude", "profiles": []})
        self.assertEqual(manifest["profiles"], [])

    def test_up_to_date_then_update_available_after_refresh(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        result = self.status_json("--refresh")
        self.assertEqual(result["status"], "up-to-date")
        self.assertIn("is up to date with origin/main", self.pakem("status", "--brief").stdout)

        self.push_upstream(version="0.3.0")
        cached = self.status_json()
        self.assertEqual(cached["status"], "up-to-date", "within the refresh window the last check is reused")

        refreshed = self.pakem("status", "--refresh")
        self.assert_ok(refreshed)
        self.assertIn("Pakem update available: 0.2.0 -> 0.3.0 on origin/main", refreshed.stdout)
        self.assertIn("changes: kernel, skill demo-skill", refreshed.stdout)
        self.assertIn("Run `pakem update` to install it.", refreshed.stdout)

    def test_offline_never_fetches(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        self.push_upstream()
        result = self.status_json("--offline")
        self.assertEqual(result["status"], "up-to-date")
        self.assertIsNone(result["framework"]["fetch"]["checked_at"])

    def test_branch_checkout_turns_the_check_off(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)
        completed = self.pakem("status", "--brief")
        self.assert_ok(completed)
        self.assertIn("update check off: the framework checkout is on branch feat/topic", completed.stdout)

    def test_not_installed(self) -> None:
        self.assertEqual(self.status_json()["status"], "not-installed")

    def test_project_pin_behind_installed_profile(self) -> None:
        profile = self.base / "profile"
        write(profile / "profile.toml", PROFILE_TOML.format(version="1.0.0"))
        write(profile / "DIGEST.md", "Work rules.\n")
        project = init_repo(self.base / "project", self.home)
        write(project / "AGENTS.md", "# Project\n\nCapabilities: none\nDelivery: nobody\n")
        self.assert_ok(self.pakem("project", "sync", str(project), "--profile", str(profile), "--apply"))
        write(profile / "profile.toml", PROFILE_TOML.format(version="1.1.0"))
        self.assert_ok(self.pakem("install", "--apply", "--profile", str(profile)))

        result = self.status_json("--offline", cwd=project)
        self.assertEqual(result["project"]["pins"], [{"profile": "work", "pinned": "1.0.0", "installed": "1.1.0"}])
        brief = self.pakem("status", "--brief", "--offline", cwd=project)
        self.assertIn("AGENTS.md pins profile work 1.0.0; installed is 1.1.0", brief.stdout)


class UpdateTests(StatusUpdateTestCase):
    def test_update_fast_forwards_installs_and_lists_files(self) -> None:
        self.assert_ok(self.pakem("install", "--apply", "--hosts", "claude"))
        upstream = self.push_upstream(kernel_line="Upstream rule.", version="0.3.0")

        planned = self.pakem("update", "--plan")
        self.assert_ok(planned)
        self.assertIn("fast-forward", planned.stdout)
        self.assertIn("pakem install --apply --hosts claude", planned.stdout)
        self.assertNotEqual(self.head(), upstream, "--plan changes nothing")

        updated = self.pakem("update", "--yes")
        self.assert_ok(updated)
        self.assertEqual(self.head(), upstream)
        self.assertEqual(self.manifest()["framework_commit"], upstream)
        self.assertIn("Upstream rule.", (self.home / ".claude" / "CLAUDE.md").read_text())
        self.assertIn("Changed: kernel, skill demo-skill", updated.stdout)
        self.assertIn("Re-read: ~/.claude/CLAUDE.md, ~/.claude/skills/demo-skill/SKILL.md", updated.stdout)
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists(), "the recorded --hosts is repeated")

        again = self.pakem("update", "--yes")
        self.assert_ok(again)
        self.assertIn("nothing to do", again.stdout)

    def test_update_refreshes_recorded_guardrails(self) -> None:
        self.assert_ok(self.pakem("install", "--apply", "--hosts", "claude"))
        self.assert_ok(self.pakem("enforce", "install", "--host", "claude", "--apply"))
        self.push_upstream()
        result = json.loads(self.pakem("update", "--yes", "--json").stdout)
        self.assertEqual(result["status"], "updated")
        self.assertIn("pakem enforce install --host claude --apply", result["done"])

    def test_release_checkout_moves_to_the_newest_release_on_main(self) -> None:
        git(self.framework, "tag", "v0.2.0", home=self.home)
        git(self.framework, "push", "-q", "origin", "v0.2.0", home=self.home)
        git(self.framework, "checkout", "-q", "v0.2.0", home=self.home)
        self.assert_ok(self.pakem("install", "--apply"))
        release = self.push_upstream(version="0.3.0", tag="v0.3.0")
        self.push_upstream(kernel_line="Unreleased rule.")

        self.assertIn("0.2.0 -> 0.3.0 on v0.3.0", self.pakem("status", "--refresh").stdout)
        self.assert_ok(self.pakem("update", "--yes"))
        self.assertEqual(self.head(), release)
        self.assertNotIn("Unreleased rule.", (self.home / ".claude" / "CLAUDE.md").read_text())

    def test_refusals_change_nothing(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        self.push_upstream()
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)
        on_branch = self.pakem("update", "--yes")
        self.assert_fails(on_branch, code=6)
        self.assertIn("installs only from merged main", on_branch.stderr)

        git(self.framework, "checkout", "-q", "main", home=self.home)
        write(self.framework / "kernel" / "KERNEL.md", "edited\n")
        self.assert_fails(self.pakem("update", "--yes"), code=6)
        git(self.framework, "checkout", "-q", "--", "kernel", home=self.home)

        self.commit(self.framework, "feat: local only")
        ahead = self.pakem("update", "--yes")
        self.assert_fails(ahead, code=6)
        self.assertIn("has commits origin/main lacks", ahead.stderr)

    def test_without_a_terminal_update_needs_yes(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        self.push_upstream()
        completed = self.pakem("update")
        self.assert_fails(completed, code=2)
        self.assertIn("pass --yes", completed.stderr)

    def test_old_manifest_with_profiles_needs_one_install(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        path = self.home / ".local" / "state" / "pakem" / "manifest.json"
        manifest = self.manifest()
        del manifest["install_options"]
        manifest["entries"].append({"path": "~/.claude/skills/x", "kind": "skill", "sha256": "0", "host": "claude", "source": "profile:work/x"})
        path.write_text(json.dumps(manifest))
        self.push_upstream()
        completed = self.pakem("update", "--yes")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED", completed.stderr)
        self.assertIn("work", completed.stderr)


class ConfigTests(StatusUpdateTestCase):
    def test_set_get_and_refuse_unknown_values(self) -> None:
        self.assertEqual(self.pakem("config", "get", "update").stdout.strip(), "notify")
        self.assert_ok(self.pakem("config", "set", "update", "apply"))
        self.assertEqual(self.pakem("config", "get", "update").stdout.strip(), "apply")
        self.assert_fails(self.pakem("config", "set", "update", "always"), code=2)
        self.assertIn('update = "apply"', (self.home / ".config" / "pakem" / "config.toml").read_text())

    def test_agent_advice_follows_the_setting(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        self.push_upstream()
        self.pakem("status", "--refresh")
        notify = self.hook("session-start", {"session_id": "s1", "cwd": str(self.base)})
        self.assertIn("do not install it yourself", notify.stdout)
        self.assert_ok(self.pakem("config", "set", "update", "apply"))
        apply = self.hook("session-start", {"session_id": "s2", "cwd": str(self.base)})
        self.assertIn("run `pakem update --yes`", apply.stdout)


class HookTests(StatusUpdateTestCase):
    def test_prompt_hook_names_files_after_a_mid_session_install(self) -> None:
        self.assert_ok(self.pakem("install", "--apply", "--hosts", "claude,codex"))
        start = self.hook("session-start", {"session_id": "abc-1", "cwd": str(self.base)})
        self.assert_ok(start)
        self.assertIn("Pakem 0.2.0 is up to date with origin/main", start.stdout)

        quiet = self.hook("prompt", {"session_id": "abc-1"})
        self.assert_ok(quiet)
        self.assertEqual(quiet.stdout, "")

        kernel = self.framework / "kernel" / "KERNEL.md"
        kernel.write_text(kernel.read_text() + "New rule.\n")
        self.commit(self.framework, "feat: new rule")
        self.assert_ok(self.pakem("install", "--apply", "--hosts", "claude,codex", "--allow-unmerged"))

        notice = self.hook("prompt", {"session_id": "abc-1"})
        self.assert_ok(notice)
        self.assertIn("Re-read before continuing: ~/.claude/CLAUDE.md", notice.stdout)
        self.assertNotIn(".codex", notice.stdout, "only the hook's own host is named")
        self.assertEqual(self.hook("prompt", {"session_id": "abc-1"}).stdout, "", "said once")

    def test_hooks_never_fail_the_session(self) -> None:
        for kind in ("session-start", "prompt"):
            with self.subTest(kind=kind):
                completed = self.hook(kind, "not json")
                self.assertEqual(completed.returncode, 0)
        unsafe = self.hook("prompt", {"session_id": "../../escape"})
        self.assertEqual(unsafe.returncode, 0)
        self.assertFalse((self.home / ".local" / "state" / "escape.json").exists())

    def test_enforce_installs_session_hooks_for_this_checkout(self) -> None:
        self.assert_ok(self.pakem("enforce", "install", "--host", "claude", "--apply"))
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        start = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        prompt = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        self.assertIn(str(self.framework / "tools" / "pakem"), start)
        self.assertTrue(start.endswith("status --hook session-start --host claude"))
        self.assertTrue(prompt.endswith("status --hook prompt --host claude"))
        self.assertNotIn("{{pakem}}", json.dumps(settings))

    def test_hooks_prefer_the_wrapper_that_runs_this_checkout(self) -> None:
        bin_dir = self.base / "bin"
        tool = self.framework / "tools" / "pakem"
        write(bin_dir / "pakem", f'#!/bin/sh\n# pakem-managed\nexec "{sys.executable}" "{tool}" "$@"\n', mode=0o755)
        env = isolated_env(self.home, {"PATH": f"{bin_dir}:/usr/bin:/bin"})
        completed = subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "enforce", "install", "--host", "claude", "--apply"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assert_ok(completed)
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(command, f"{bin_dir / 'pakem'} status --hook session-start --host claude")


class CacheTests(StatusUpdateTestCase):
    def test_damaged_cache_counts_as_never_checked(self) -> None:
        self.assert_ok(self.pakem("install", "--apply"))
        write(self.home / ".local" / "state" / "pakem" / "upstream.json", '{"torn')
        result = self.status_json()
        self.assertEqual(result["status"], "up-to-date")
        self.assertIsNotNone(result["framework"]["fetch"]["checked_at"], "a damaged cache triggers a fresh check")
