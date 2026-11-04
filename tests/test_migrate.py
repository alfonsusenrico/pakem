"""`pakem migrate export|import`: a workspace from one device to the next."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
import json
import os
import stat
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

from support import IsolatedTestCase, REPO_ROOT, git, init_repo, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
from pakem_lib import guided, migrate  # noqa: E402


class MigrateTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.old_home = self.base / "old-home"
        self.new_home = self.base / "new-home"
        self.old_home.mkdir()
        self.new_home.mkdir()
        self.remotes = self.base / "remotes"
        self.use(self.old_home)
        self.project = self.repository("work/app", "app")
        write(self.project / "PROJECT_STATE.md", "# State\nbranch main, next: ship it\n")
        write(self.project / "JOURNAL.md", "# Journal\n")
        write(self.project / ".git" / "info" / "exclude", "PROJECT_STATE.md\nJOURNAL.md\n")
        profiles = self.repository("profiles-repo", "profiles")
        write(profiles / "personal" / "profile.toml", 'name = "personal"\n')
        git(profiles, "add", "-A", home=self.old_home)
        git(profiles, "commit", "-q", "-m", "chore: profile", home=self.old_home)
        git(profiles, "push", "-q", "origin", "main", home=self.old_home)
        config = self.old_home / ".config" / "pakem"
        write(config / "config.toml", 'forges = "both"\n')
        write(config / "device.toml", 'name = "old-mac"\nkey_fingerprint = "SHA256:old"\n')
        write(config / "hosts.toml", '[hosts."box"]\ntarget = "admin@203.0.113.7"\nprojects = ["app"]\n'
                                     f'project_dirs = ["{self.old_home}/work/app"]\n')
        write(config / "report.toml", f'repos_dirs = []\nrepos = ["{self.project}"]\n')
        store = self.old_home / "documents"
        write(store / "config.toml", "[numbering]\nlast = 3\n")
        write(config / "documents.toml", f'root = "{store}"\n')
        write(self.old_home / ".local" / "state" / "pakem" / "manifest.json", json.dumps({
            "schema": 1, "framework_version": "0.2.0", "entries": [], "backups": [],
            "install_options": {"hosts": "claude", "profiles": [str(profiles / "personal")]}}))
        write(migrate.claude_folder(self.project) / "memory" / "MEMORY.md", "- a note\n")

    def use(self, home: Path) -> None:
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def repository(self, relative: str, name: str) -> Path:
        remote = self.remotes / f"{name}.git"
        git(self.base, "init", "-q", "--bare", "-b", "main", str(remote), home=self.old_home)
        repo = init_repo(self.old_home / relative, self.old_home)
        write(repo / "README.md", f"{name}\n")
        git(repo, "add", "-A", home=self.old_home)
        git(repo, "commit", "-q", "-m", "chore: start", home=self.old_home)
        git(repo, "remote", "add", "origin", str(remote), home=self.old_home)
        git(repo, "push", "-q", "-u", "origin", "main", home=self.old_home)
        return repo

    def export(self, *options: str) -> Path:
        target = self.base / "bundle.tar.gz"
        args = argparse.Namespace(to=str(target), agent_sessions="--agent-sessions" in options, plan=False)
        with redirect_stdout(io.StringIO()):
            migrate.run_export(args)
        return target

    def import_on_new_device(self, bundle: Path) -> None:
        mock.patch.dict(os.environ, {"HOME": str(self.new_home)}).start()
        self.addCleanup(mock.patch.stopall)
        with redirect_stdout(io.StringIO()):
            migrate.run_import(argparse.Namespace(bundle=str(bundle), plan=False, yes=True))

    def test_a_workspace_moves_to_a_new_home(self) -> None:
        bundle = self.export("--agent-sessions")
        self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600, "the bundle is private")
        self.import_on_new_device(bundle)
        app = self.new_home / "work" / "app"
        self.assertEqual((app / "README.md").read_text(), "app\n", "cloned to the same place")
        self.assertIn("next: ship it", (app / "PROJECT_STATE.md").read_text())
        self.assertIn("PROJECT_STATE.md", (app / ".git" / "info" / "exclude").read_text())
        config = self.new_home / ".config" / "pakem"
        self.assertEqual((config / "config.toml").read_text(), 'forges = "both"\n')
        self.assertFalse((config / "device.toml").exists(), "the old device's description stays behind")
        hosts = (config / "hosts.toml").read_text()
        self.assertNotIn("projects = ", hosts, "the old device's key allowances do not travel")
        self.assertIn(f'project_dirs = ["{self.new_home}/work/app"]', hosts)
        self.assertIn(f'"{self.project}"'.replace(str(self.old_home), str(self.new_home)),
                      (config / "report.toml").read_text(), "paths under the old home land under the new one")
        self.assertTrue((self.new_home / "profiles-repo" / "personal" / "profile.toml").is_file())
        self.assertIn("last = 3", (self.new_home / "documents" / "config.toml").read_text())
        memory = migrate.claude_folder(app) / "memory" / "MEMORY.md"
        self.assertEqual(memory.read_text(), "- a note\n")
        migrated = json.loads((self.new_home / ".local" / "state" / "pakem" / migrate.MIGRATED_INSTALL).read_text())
        self.assertEqual(migrated["hosts"], "claude")

    def test_import_never_overwrites_what_is_already_here(self) -> None:
        bundle = self.export()
        app = self.new_home / "work" / "app"
        write(self.new_home / ".config" / "pakem" / "config.toml", 'forges = "github"\n')
        self.import_on_new_device(bundle)
        write(app / "PROJECT_STATE.md", "# State on the new device\n")
        with redirect_stdout(io.StringIO()):
            migrate.run_import(argparse.Namespace(bundle=str(bundle), plan=False, yes=True))
        self.assertEqual((self.new_home / ".config" / "pakem" / "config.toml").read_text(), 'forges = "github"\n')
        self.assertEqual((app / "PROJECT_STATE.md").read_text(), "# State on the new device\n")

    def test_sessions_and_secrets_stay_out_unless_asked(self) -> None:
        write(self.project / ".env", "TOKEN=x\n")
        bundle = self.export()
        with tarfile.open(bundle) as archive:
            names = archive.getnames()
        self.assertFalse([name for name in names if name.startswith("sessions/")])
        self.assertFalse([name for name in names if ".env" in name or "device.toml" in name])

    def test_an_unsafe_bundle_is_refused(self) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            for name, data in (("manifest.json", json.dumps({"format": migrate.FORMAT}).encode()),
                               ("../escape", b"x")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        bad = self.base / "bad.tar.gz"
        bad.write_bytes(payload.getvalue())
        with self.assertRaisesRegex(migrate.PakemError, "unexpected entry"):
            migrate.read_bundle(bad)

    def test_the_guided_setup_imports_first_and_offers_the_old_answers(self) -> None:
        bundle = self.export()
        mock.patch.dict(os.environ, {"HOME": str(self.new_home)}).start()
        self.addCleanup(mock.patch.stopall)
        ran: list[list[str]] = []

        def run(command: list[str]) -> int:
            ran.append(command)
            if "migrate" in command:
                migrate.import_bundle(Path(command[command.index("import") + 1]))
            return 0

        parser = argparse.ArgumentParser()
        guided.add_arguments(parser)
        args = parser.parse_args(["--import", str(bundle), "--yes", "--no-tools", "--no-guardrails"])
        args.root = str(self.framework)
        prompt = guided.Prompter(False, write=lambda text: None)
        checks = mock.Mock(checks=[], owner_actions=[], outcome=lambda: 0)
        with mock.patch.object(guided, "assess", return_value=checks), \
                mock.patch.object(guided, "load_profile"), \
                mock.patch.object(guided, "detected_forges", return_value="github"), \
                mock.patch.object(guided.sys.stdin, "isatty", return_value=True):
            guided.run_guided(args, prompt, run=run)
        self.assertIn("migrate", ran[0], "the bundle is imported before anything else")
        install = next(command for command in ran if "install" in command and "migrate" not in command)
        self.assertEqual(install[install.index("--hosts") + 1], "claude", "the old device's agents")
        self.assertIn(str(self.new_home / "profiles-repo" / "personal"), install)


if __name__ == "__main__":
    unittest.main()
