"""pakem setup device and setup check, with fake package manager, runtime, and forge CLIs.

PATH holds only a fake bin directory plus /usr/bin and /bin, so git and ssh-keygen are
real and everything the setup installs or probes beyond them is a logged fake.
"""

from __future__ import annotations

import json
import platform
import stat
import subprocess
import sys
import unittest

from support import PAKEM, IsolatedTestCase, git, init_repo, isolated_env, write

# Fake Homebrew: logs, and "installs" each package as a fake tool.
FAKE_BREW = """#!/bin/sh
echo "brew $*" >> "{log}"
if [ "$1" = install ]; then
  shift
  for package in "$@"; do ln -sf "{bin}/fake-tool" "{bin}/$package"; done
fi
"""
# Every installed fake logs; docker info works once Colima runs; auth follows a marker.
FAKE_TOOL = """#!/bin/sh
name="$(basename "$0")"
echo "$name $*" >> "{log}"
case "$name $1" in
  "colima start") touch "{state}/colima-running" ;;
  "docker info") [ -e "{state}/colima-running" ] || exit 1; echo "colima" ;;
  "gh auth"|"glab auth") [ -e "{state}/$name-logged-in" ] || exit 1; echo "Logged in" ;;
  *) echo "$name version 1.0" ;;
esac
"""


@unittest.skipUnless(platform.system() == "Darwin", "the macOS path uses Homebrew")
class SetupDeviceMacTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        self.bin = self.base / "bin"
        self.state = self.base / "fake-state"
        self.state.mkdir()
        self.log = self.base / "calls.log"
        self.log.write_text("")
        values = {"log": self.log, "bin": self.bin, "state": self.state}
        write(self.bin / "fake-tool", FAKE_TOOL.format(**values), mode=0o755)
        write(self.bin / "brew", FAKE_BREW.format(**values), mode=0o755)

    def install_fake(self, *names: str) -> None:
        for name in names:
            (self.bin / name).symlink_to(self.bin / "fake-tool")

    def run_setup(self, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
        env = isolated_env(self.home, {"PATH": path or f"{self.bin}:/usr/bin:/bin"})
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "setup", *args],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line]

    def key(self):
        return self.home / ".ssh" / "pakem_device_ed25519"

    def test_bare_device_installs_everything_once_then_asks_for_logins(self) -> None:
        check = self.run_setup("check")
        self.assert_fails(check, code=5)
        self.assertIn("FAIL  gh: missing; `pakem setup device` installs package gh", check.stdout)
        self.assertEqual(self.calls(), [], "setup check changes nothing")

        planned = self.run_setup("device", "--plan")
        self.assert_ok(planned)
        self.assertIn("brew install gh glab uv docker colima", planned.stdout)
        self.assertFalse(self.key().exists())

        done = self.run_setup("device", "--yes")
        self.assert_fails(done, code=3)
        self.assertIn("brew install gh glab uv docker colima", self.calls())
        self.assertIn("colima start", self.calls())
        self.assertIn("OWNER ACTION REQUIRED: run `gh auth login` in your terminal", done.stdout)
        self.assertIn("OWNER ACTION REQUIRED: run `glab auth login` in your terminal", done.stdout)
        self.assertTrue(self.key().exists())
        self.assertEqual(stat.S_IMODE(self.key().stat().st_mode), 0o600)
        device = (self.home / ".config" / "pakem" / "device.toml").read_text()
        self.assertIn('key = "~/.ssh/pakem_device_ed25519"', device)
        self.assertIn('key_fingerprint = "SHA256:', device)
        self.assertNotIn("PRIVATE KEY", done.stdout + done.stderr + device)
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").exists(), "the framework is installed")

        for name in ("gh", "glab"):
            (self.state / f"{name}-logged-in").touch()
        self.log.write_text("")
        again = self.run_setup("device", "--yes", "--json")
        self.assert_ok(again)
        result = json.loads(again.stdout)
        self.assertEqual(result["status"], "done")
        self.assertTrue(all(check["ok"] for check in result["checks"]))
        self.assertFalse([call for call in self.calls() if call.startswith(("brew install", "colima start"))])

    def test_a_running_runtime_is_kept(self) -> None:
        self.install_fake("gh", "glab", "uv", "docker")
        (self.state / "colima-running").touch()
        planned = self.run_setup("device", "--plan")
        self.assert_ok(planned)
        self.assertNotIn("colima", planned.stdout)
        self.assertNotIn("brew install", planned.stdout)

    def test_without_homebrew_the_owner_installs_it(self) -> None:
        (self.bin / "brew").unlink()
        completed = self.run_setup("device", "--yes")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: install Homebrew (https://brew.sh)", completed.stdout)
        self.assertIn("OWNER ACTION REQUIRED: install uv (https://docs.astral.sh/uv/", completed.stdout)

    def test_without_a_terminal_setup_needs_yes(self) -> None:
        completed = self.run_setup("device")
        self.assert_fails(completed, code=2)
        self.assertIn("pass --yes", completed.stderr)
        self.assertEqual(self.calls(), [])

    def test_half_a_key_pair_is_refused(self) -> None:
        write(self.key().with_name("pakem_device_ed25519.pub"), "ssh-ed25519 AAAA test\n")
        completed = self.run_setup("device", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("move it away", completed.stderr)
        self.assertFalse(self.key().exists())
        self.assertEqual(self.calls(), [])


class InstallCommandTests(unittest.TestCase):
    def test_linux_uses_apt_with_sudo(self) -> None:
        sys.path.insert(0, str(PAKEM.parent))
        from pakem_lib.setup_device import LINUX, DevicePlan, _install_command

        plan = DevicePlan(LINUX, "x86_64", packages=["gh", "docker.io"])
        self.assertEqual(
            _install_command(plan),
            [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "gh", "docker.io"]],
        )
