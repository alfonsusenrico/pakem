"""pakem setup device, setup key, and setup check, with fake package manager, runtime, and CLIs.

PATH holds only a fake bin directory plus /usr/bin and /bin, so git and ssh-keygen are
real and everything else the setup installs or probes is a logged fake. A fake ssh-add
keeps the tests away from the real agent and the real Keychain.
"""

from __future__ import annotations

import json
import os
import platform
import pty
import select
import stat
import subprocess
import sys
import time
import unittest

from support import PAKEM, IsolatedTestCase, git, init_repo, isolated_env, write

# Fake Homebrew: logs, and "installs" each package as a fake tool.
FAKE_BREW = """#!/bin/sh
echo "brew $*" >> "{log}"
# Real installers print progress on stdout.
echo "==> brew $*"
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
# Fake ssh-agent client: -L lists what was added; no agent until the marker exists.
FAKE_SSH_ADD = """#!/bin/sh
echo "ssh-add $*" >> "{log}"
[ -e "{state}/agent" ] || exit 2
case "$1" in
  -L) [ -s "{state}/agent" ] || exit 1; cat "{state}/agent" ;;
  *) cat "$1.pub" >> "{state}/agent" ;;
esac
"""


class SetupTestCase(IsolatedTestCase):
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
        write(self.bin / "ssh-add", FAKE_SSH_ADD.format(**values), mode=0o755)
        self.path = f"{self.bin}:/usr/bin:/bin"

    def install_fake(self, *names: str) -> None:
        for name in names:
            (self.bin / name).symlink_to(self.bin / "fake-tool")

    def run_setup(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "setup", *args],
            capture_output=True,
            text=True,
            env=isolated_env(self.home, {"PATH": self.path}),
            stdin=subprocess.DEVNULL,
        )

    def run_tty(self, *args: str) -> tuple[int, str]:
        """Run `pakem setup` on a pseudo-terminal, as the owner would."""
        env = isolated_env(self.home, {"PATH": self.path})
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(PAKEM), "--root", str(self.framework), "setup", *args], env)
        output, deadline = b"", time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        else:
            os.kill(pid, 9)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), output.decode(errors="replace")

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line]

    def key(self):
        return self.home / ".ssh" / "pakem_device_ed25519"

    def device_toml(self) -> str:
        return (self.home / ".config" / "pakem" / "device.toml").read_text()


@unittest.skipUnless(platform.system() == "Darwin", "the macOS path uses Homebrew")
class SetupDeviceMacTests(SetupTestCase):
    def test_agent_run_installs_and_leaves_the_owner_only_steps(self) -> None:
        check = self.run_setup("check")
        self.assert_fails(check, code=3)
        self.assertIn("FAIL  gh: missing. Pakem installs package gh", check.stdout)
        self.assertEqual(self.calls(), [], "setup check changes nothing")

        planned = self.run_setup("device", "--plan")
        self.assert_ok(planned)
        self.assertIn("brew install gh glab uv docker colima", planned.stdout)
        self.assertFalse([call for call in self.calls() if not call.endswith(("--version", "version"))],
                         "--plan installs and starts nothing")
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup key` in your terminal", planned.stdout)

        done = self.run_setup("device", "--yes")
        self.assert_fails(done, code=3)
        self.assertIn("brew install gh glab uv docker colima", self.calls())
        self.assertIn("colima start", self.calls())
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup key` in your terminal", done.stdout)
        self.assertIn("OWNER ACTION REQUIRED: run `gh auth login` in your terminal", done.stdout)
        self.assertFalse(self.key().exists(), "without a terminal nobody can type the passphrase")
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").exists(), "the framework is installed")

        code, made = self.run_tty("key", "--no-passphrase")
        self.assertEqual(code, 0, made)
        self.assertEqual(stat.S_IMODE(self.key().stat().st_mode), 0o600)
        self.assertIn("key_passphrase = false", self.device_toml())
        self.assertIn('key_fingerprint = "SHA256:', self.device_toml())
        self.assertNotIn("PRIVATE KEY", made + self.device_toml())

        for name in ("gh", "glab"):
            (self.state / f"{name}-logged-in").touch()
        self.log.write_text("")
        again = self.run_setup("device", "--yes", "--json")
        self.assert_ok(again)
        result = json.loads(again.stdout)
        self.assertEqual(result["status"], "done")
        self.assertTrue(all(check["ok"] for check in result["checks"]), result["checks"])
        self.assertFalse([call for call in self.calls() if call.startswith(("brew install", "colima start"))])

    def test_a_running_runtime_is_kept(self) -> None:
        self.install_fake("gh", "glab", "uv", "docker")
        (self.state / "colima-running").touch()
        planned = self.run_setup("device", "--plan")
        self.assert_ok(planned)
        self.assertNotIn("colima", planned.stdout)
        self.assertNotIn("brew install", planned.stdout)

    def test_an_installed_docker_app_is_started_by_the_owner_not_replaced(self) -> None:
        self.install_fake("gh", "glab", "uv", "docker")
        (self.home / "Applications" / "Docker.app").mkdir(parents=True)
        planned = self.run_setup("device", "--plan")
        self.assert_ok(planned)
        self.assertIn("OWNER ACTION REQUIRED: start Docker, then run the command again", planned.stdout)
        self.assertNotIn("colima", planned.stdout)

    def test_json_stays_parseable_while_installers_print(self) -> None:
        completed = self.run_setup("device", "--yes", "--json")
        self.assertIn(completed.returncode, (0, 3), completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "owner-action")

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


class SetupKeyTests(SetupTestCase):
    def test_half_a_key_pair_is_refused(self) -> None:
        write(self.key().with_name("pakem_device_ed25519.pub"), "ssh-ed25519 AAAA test\n")
        completed = self.run_setup("device", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("Move it away", completed.stderr)
        self.assertFalse(self.key().exists())

    def test_no_passphrase_is_the_owners_choice(self) -> None:
        completed = self.run_setup("key", "--no-passphrase")
        self.assert_fails(completed, code=3)
        self.assertIn("a device key without a passphrase is your choice", completed.stderr)
        self.assertFalse(self.key().exists())

    def test_key_plan_and_json(self) -> None:
        planned = self.run_setup("key", "--plan")
        self.assert_ok(planned)
        self.assertIn("ssh-keygen asks for its passphrase", planned.stdout)
        self.assertFalse(self.key().exists())
        self.assertEqual(self.calls(), [])
        result = self.run_setup("key", "--json")
        self.assert_fails(result, code=3)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "owner-action")
        self.assertTrue(parsed["owner_actions"][0].startswith("OWNER ACTION REQUIRED: run `pakem setup key`"))

    def test_a_passphrase_needs_the_owner_terminal(self) -> None:
        completed = self.run_setup("key")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup key` in your terminal", completed.stderr)
        self.assertFalse(self.key().exists())

    def test_locked_key_asks_for_ssh_add(self) -> None:
        self.key().parent.mkdir(mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "pass-phrase", "-f", str(self.key())], check=True)
        (self.state / "agent").touch()
        check = self.run_setup("check")
        self.assertIn("FAIL  device key unlocked: not in ssh-agent", check.stdout)
        self.assertIn(f"run `ssh-add {self.key()}` in your terminal", check.stdout)

        (self.state / "agent").write_text(self.key().with_name("pakem_device_ed25519.pub").read_text())
        unlocked = self.run_setup("check")
        self.assertIn("ok    device key unlocked: held by ssh-agent", unlocked.stdout)

        (self.state / "agent").unlink()
        no_agent = self.run_setup("check")
        self.assertIn("OWNER ACTION REQUIRED: start an ssh-agent", no_agent.stdout)

    def test_owner_terminal_creates_a_key_with_a_passphrase(self) -> None:
        (self.state / "agent").touch()
        env = isolated_env(self.home, {"PATH": self.path})
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(PAKEM), "--root", str(self.framework), "setup", "key"], env)
        output, answered_upto, deadline = b"", 0, time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            # Answer each complete passphrase prompt once; ssh-keygen turns echo off first.
            prompt = output[answered_upto:]
            if b"passphrase" in prompt.lower() and prompt.rstrip().endswith(b":"):
                os.write(fd, b"test-pass-phrase\n")
                answered_upto = len(output)
        else:
            os.kill(pid, 9)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors="replace"))
        locked = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(self.key())], capture_output=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(locked.returncode, 0, "the key has a passphrase")
        self.assertIn("key_passphrase = true", self.device_toml())
        self.assertIn(f"ssh-add {self.key()}", self.calls())
        self.assertNotIn(b"test-pass-phrase", output, "the passphrase is never echoed")


class InstallCommandTests(unittest.TestCase):
    def test_linux_uses_apt_with_sudo(self) -> None:
        sys.path.insert(0, str(PAKEM.parent))
        from pakem_lib.requirements import LINUX, Assessment, Device, _install_commands

        assessment = Assessment(Device(LINUX, True, "x86_64"), interactive=False, packages=["gh", "docker.io"])
        self.assertEqual(
            _install_commands(assessment),
            [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "gh", "docker.io"]],
        )
        self.assertEqual(Device(LINUX, True, "x86_64").label, "linux (WSL), x86_64")
