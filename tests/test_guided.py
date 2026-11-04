"""`pakem setup`, guided, and the one-line installer that ends in it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import FIXTURE_VERSION, PAKEM, REPO_ROOT, IsolatedTestCase, git, init_repo, isolated_env, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
from pakem_lib import guided  # noqa: E402

PROFILE_TOML = 'name = "{name}"\nversion = "1.0.0"\nrequires_framework = ">=0.1"\ndigest = "digest.md"\n'


class Scripted(guided.Prompter):
    """Answers in order, by question; an unscripted question takes its default."""

    def __init__(self, answers: dict[str, str]):
        super().__init__(True, read=self.answer, write=self.say)
        self.answers = answers
        self.asked: list[str] = []
        self.said: list[str] = []

    def answer(self, question: str) -> str:
        self.asked.append(question)
        for start, value in self.answers.items():
            if question.startswith(start):
                return value
        return ""

    def say(self, text: str) -> None:
        self.said.append(text)


def arguments(**options) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    guided.add_arguments(parser)
    args = parser.parse_args([])
    for key, value in options.items():
        setattr(args, key, value)
    return args


class GuidedSetupTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ran: list[list[str]] = []
        self.environment = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        checks = SimpleNamespace(checks=[], owner_actions=[], outcome=lambda: 0)
        assess = mock.patch.object(guided, "assess", return_value=checks)
        assess.start()
        self.addCleanup(assess.stop)
        forges = mock.patch.object(guided, "detected_forges", return_value="github")
        forges.start()
        self.addCleanup(forges.stop)

    def profile(self, folder: Path, name: str) -> Path:
        write(folder / "profile.toml", PROFILE_TOML.format(name=name))
        write(folder / "digest.md", f"Rules of {name}.\n")
        return folder

    def run_setup(self, prompt: guided.Prompter, **options) -> int:
        args = arguments(root=str(self.framework), no_tools=True, **options)
        with mock.patch.object(guided.sys.stdin, "isatty", return_value=True):
            return guided.run_guided(args, prompt, run=lambda command: self.ran.append(command) or 0)

    def installs(self) -> list[list[str]]:
        return [command[command.index("install"):] for command in self.ran if "install" in command
                and "enforce" not in command]

    def test_a_fresh_device_is_set_up_from_the_answers(self) -> None:
        (self.home / ".claude").mkdir()
        mine = self.profile(self.base / "profiles" / "mine", "mine")
        prompt = Scripted({"Agents to set up": "", "Profiles to add": str(mine), "Forges": "both",
                           "Does this device": "no", "Updates:": "off"})
        self.assertEqual(self.run_setup(prompt), 0)
        self.assertEqual(self.installs(), [["install", "--apply", "--hosts", "claude", "--profile", str(mine)]])
        self.assertIn(["enforce", "install", "--host", "claude", "--apply"],
                      [command[command.index("enforce"):] for command in self.ran if "enforce" in command])
        config = tomllib.loads((self.home / ".config" / "pakem" / "config.toml").read_text())
        self.assertEqual(config, {"containers": "no", "forges": "both", "update": "off"})
        self.assertTrue(any(line.startswith("Agents: claude") for line in prompt.said), "found agents are named")

    def test_a_repository_of_profiles_is_cloned_once_and_each_profile_found(self) -> None:
        source = init_repo(self.base / "pakem-profiles", self.home)
        self.profile(source / "personal", "personal")
        self.profile(source / "freelance", "freelance")
        git(source, "add", "-A", home=self.home)
        git(source, "commit", "-q", "-m", "chore: profiles", home=self.home)
        url = f"file://{source}"
        clones = self.base / "clones"
        self.run_setup(Scripted({"Profiles to add": url}), profiles_dir=str(clones))
        self.assertEqual(self.installs()[0][-4:], ["--profile", str(clones / "pakem-profiles" / "freelance"),
                                                    "--profile", str(clones / "pakem-profiles" / "personal")])
        self.ran.clear()
        self.run_setup(Scripted({"Profiles to add": url}), profiles_dir=str(clones))
        self.assertEqual(len(self.installs()[0]), 8, "a second run updates the clone and adds nothing twice")

    def test_a_new_profile_starts_from_the_example(self) -> None:
        example = self.profile(self.framework / "profiles" / "example", "example")
        write(example / "digest.md", "Team rules (Pakem profile `example`):\n")
        target = self.base / "mine"
        prompt = Scripted({"Start your own profile": "y", "Its name": "acme", "Its folder": str(target)})
        self.run_setup(prompt)
        self.assertIn('name = "acme"', (target / "profile.toml").read_text())
        self.assertIn("`acme`", (target / "digest.md").read_text())
        self.assertIn(str(target), self.installs()[0])

    def test_reconfiguring_offers_the_current_answers(self) -> None:
        mine = self.profile(self.base / "profiles" / "mine", "mine")
        write(self.home / ".local" / "state" / "pakem" / "manifest.json", json.dumps({
            "schema": 1, "framework_version": FIXTURE_VERSION,
            "install_options": {"hosts": "codex", "profiles": [str(mine)]}, "entries": [], "backups": []}))
        write(self.home / ".config" / "pakem" / "config.toml", 'forges = "gitlab"\n')
        prompt = Scripted({})
        self.run_setup(prompt)
        self.assertEqual(self.installs(), [["install", "--apply", "--hosts", "codex", "--profile", str(mine)]])
        self.assertTrue(any("Reconfiguring" in line for line in prompt.said))
        self.assertIn('forges = "gitlab"', (self.home / ".config" / "pakem" / "config.toml").read_text())

    def test_yes_takes_the_flags_and_asks_nothing(self) -> None:
        mine = self.profile(self.base / "profiles" / "mine", "mine")
        prompt = Scripted({})
        prompt.interactive = False
        self.run_setup(prompt, yes=True, hosts="claude,codex", profile=[str(mine)], no_guardrails=True)
        self.assertEqual(prompt.asked, [])
        self.assertEqual(self.installs(), [["install", "--apply", "--hosts", "claude,codex", "--profile", str(mine)]])
        self.assertFalse([command for command in self.ran if "enforce" in command])

    def test_a_folder_without_a_profile_is_refused_before_anything_changes(self) -> None:
        (self.base / "empty").mkdir()
        with self.assertRaisesRegex(guided.PakemError, "holds no profile"):
            self.run_setup(Scripted({"Profiles to add": str(self.base / "empty")}))
        self.assertEqual(self.ran, [])

    def test_declining_the_plan_changes_nothing(self) -> None:
        with self.assertRaises(guided.CommandRefused):
            self.run_setup(Scripted({"Go ahead": "n"}))
        self.assertEqual(self.ran, [])
        self.assertFalse((self.home / ".config" / "pakem" / "config.toml").exists())


class GuidedCommandTests(IsolatedTestCase):
    def test_without_a_terminal_it_needs_yes(self) -> None:
        completed = self.pakem("setup")
        self.assert_fails(completed, code=2)
        self.assertIn("pass --yes", completed.stderr)


class InstallerTests(IsolatedTestCase):
    """install.sh against a local repository whose `pakem` only records how it was called."""

    def setUp(self) -> None:
        super().setUp()
        self.upstream = init_repo(self.base / "upstream", self.home)
        recorder = f'import sys\nopen({str(self.base / "calls.log")!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
        for name in ("pakem", "pk"):
            write(self.upstream / "tools" / name, recorder)
        git(self.upstream, "add", "-A", home=self.home)
        git(self.upstream, "commit", "-q", "-m", "chore: release", home=self.home)
        git(self.upstream, "tag", "v1.0.0", home=self.home)
        self.data = self.base / "data"
        self.bin = self.base / "bin"

    def install(self, *options: str) -> subprocess.CompletedProcess:
        env = isolated_env(self.home, {"PAKEM_REPO": str(self.upstream), "PAKEM_HOME": str(self.data),
                                       "PAKEM_BIN": str(self.bin)})
        return subprocess.run(["sh", str(REPO_ROOT / "install.sh"), *options], capture_output=True, text=True,
                              env=env, stdin=subprocess.DEVNULL)

    def calls(self) -> list[str]:
        path = self.base / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def test_the_one_liner_installs_the_release_and_runs_setup(self) -> None:
        completed = self.install("--yes")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(str(self.data / "source" / "tools" / "pakem"), (self.bin / "pakem").read_text())
        self.assertEqual(self.calls(), ["setup --yes"])

    def test_without_a_terminal_or_yes_it_says_how_to_finish(self) -> None:
        completed = self.install()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("pakem setup --yes", completed.stdout)
        self.assertEqual(self.calls(), [], "nothing is set up without an answer")

    def test_a_checkout_that_follows_main_keeps_following_it(self) -> None:
        self.assertEqual(self.install("--yes").returncode, 0)
        source = self.data / "source"
        git(source, "checkout", "-q", "main", home=self.home)
        completed = self.install("--yes")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Keeping", completed.stdout)
        self.assertEqual(git(source, "symbolic-ref", "--short", "HEAD", home=self.home).stdout.strip(), "main")

    def test_an_existing_checkout_is_used_as_it_is(self) -> None:
        completed = self.install("--source", str(self.upstream), "--yes")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(str(self.upstream / "tools" / "pakem"), (self.bin / "pakem").read_text())
        self.assertFalse((self.data / "source").exists(), "nothing is downloaded")


if __name__ == "__main__":
    unittest.main()
