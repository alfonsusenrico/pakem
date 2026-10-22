"""pakem setup host, setup check --host, and host runner, against a fake SSH host.

The fake ssh runs the remote command locally with a fake sudo first on PATH; sudo only
logs (and keeps what it reads from a non-terminal stdin), so no bootstrap ever runs here.
These commands require a terminal, so they run under a pseudo-terminal.
"""

from __future__ import annotations

import os
import pty
import re
import select
import sys
import time
import tomllib

from support import PAKEM, IsolatedTestCase, git, init_repo, isolated_env, write

FAKE_SSH = """#!/bin/sh
echo "ssh $*" >> "{log}"
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-p) shift 2 ;;
    -O) exit 0 ;;
    -t) shift ;;
    *) break ;;
  esac
done
shift
PATH="{remote_bin}:$PATH" exec sh -c "$*"
"""
FAKE_SUDO = """#!/bin/sh
echo "sudo $*" >> "{log}"
for argument in "$@"; do
  case "$argument" in
    */af_bootstrap.sh)
      [ -f "$argument" ] && echo "present bootstrap" >> "{log}"
      [ -f "$(dirname "$argument")/../../../../VERSION" ] && echo "present VERSION" >> "{log}" ;;
  esac
done
[ -t 0 ] || cat > "{state}/sudo-stdin"
[ -f "{state}/sudo-exit" ] && exit "$(cat "{state}/sudo-exit")"
exit 0
"""
FAKE_GH = """#!/bin/sh
echo "gh $*" >> "{log}"
case "$1" in
  api) printf 'TOKEN-SECRET-123\\n' ;;
  *) echo "gh version 1.0" ;;
esac
"""
TOKEN = "TOKEN-SECRET-123"


class SetupHostTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        assets = self.framework / "skills" / "release-delivery" / "assets" / "deploy"
        write(assets / "af_bootstrap.sh", "#!/bin/sh\nexit 0\n")
        write(assets / "af_runner.sh", "#!/bin/sh\nexit 0\n")
        write(assets / "systemd" / "af-agent.timer", "[Timer]\n")
        write(assets / "__pycache__" / "skip.pyc", "x")
        init_repo(self.framework, self.home)
        git(self.framework, "add", "-A", home=self.home)
        git(self.framework, "commit", "-q", "-m", "chore: fixture", home=self.home)
        self.bin = self.base / "bin"
        self.remote_bin = self.base / "remote-bin"
        self.state = self.base / "fake-state"
        self.state.mkdir()
        self.log = self.base / "calls.log"
        self.log.write_text("")
        values = {"log": self.log, "remote_bin": self.remote_bin, "state": self.state}
        write(self.bin / "ssh", FAKE_SSH.format(**values), mode=0o755)
        write(self.bin / "gh", FAKE_GH.format(**values), mode=0o755)
        write(self.remote_bin / "sudo", FAKE_SUDO.format(**values), mode=0o755)
        self.env = isolated_env(self.home, {"PATH": f"{self.bin}:/usr/bin:/bin"})

    def run_tty(self, *args: str) -> tuple[int, str]:
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(PAKEM), "--root", str(self.framework), *args], self.env)
        output, deadline = b"", time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
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

    def hosts(self) -> dict:
        return tomllib.loads((self.home / ".config" / "pakem" / "hosts.toml").read_text())["hosts"]

    def test_setup_uploads_runs_the_bootstrap_and_records_the_host(self) -> None:
        code, output = self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--runner", "--yes")
        self.assertEqual(code, 0, output)
        calls = self.calls()
        install = next(call for call in calls if call.startswith("sudo bash") and "af_bootstrap.sh install" in call)
        self.assertTrue(install.endswith("install --trigger pull --deploy-root /srv/deploy"), install)
        self.assertIn("present bootstrap", calls)
        self.assertIn("present VERSION", calls, "the bootstrap finds VERSION four levels up")
        self.assertTrue([call for call in calls if call.startswith("sudo bash") and call.endswith("af_runner.sh install")])
        directory = re.search(r"(/tmp/pakem-host\.\w+)/", install).group(1)
        self.assertFalse(os.path.exists(directory), "the upload is removed")
        self.assertEqual(
            {key: value for key, value in self.hosts()["prod"].items() if key != "set_up_at"},
            {"target": "admin@203.0.113.7", "deploy_root": "/srv/deploy", "trigger": "pull", "proxy": True, "runner": True},
        )

    def test_without_a_terminal_nothing_connects(self) -> None:
        completed = self.pakem("setup", "host", "admin@203.0.113.7", "--yes")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup host admin@203.0.113.7", completed.stderr)
        self.assertEqual(self.calls(), [])
        planned = self.pakem("setup", "host", "admin@203.0.113.7", "--no-proxy", "--plan")
        self.assert_ok(planned)
        self.assertIn("--no-proxy", planned.stdout)
        self.assertEqual(self.calls(), [])

    def test_failed_host_check_exits_5_and_still_records(self) -> None:
        (self.state / "sudo-exit").write_text("1")
        code, output = self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--yes")
        self.assertEqual(code, 5, output)
        self.assertIn("prod", self.hosts())

    def test_host_check_uses_the_recorded_flags(self) -> None:
        code, output = self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--no-proxy", "--yes")
        self.assertEqual(code, 0, output)
        self.log.write_text("")
        code, output = self.run_tty("setup", "check", "--host", "prod")
        self.assertEqual(code, 0, output)
        check = next(call for call in self.calls() if "af_bootstrap.sh check" in call)
        self.assertTrue(check.endswith("check --trigger pull --deploy-root /srv/deploy --no-proxy"), check)
        (self.state / "sudo-exit").write_text("1")
        code, _ = self.run_tty("setup", "check", "--host", "prod")
        self.assertEqual(code, 5)

    def test_runner_token_reaches_sudo_on_stdin_only(self) -> None:
        self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "plain", "--yes")
        refused = self.pakem("host", "runner", "add", "plain", "me/app", "--yes")
        self.assert_fails(refused, code=6)
        self.assertIn("--runner", refused.stderr)

        self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--runner", "--yes")
        self.log.write_text("")
        code, output = self.run_tty("host", "runner", "add", "prod", "me/app", "--yes")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.state / "sudo-stdin").read_text().strip(), TOKEN)
        self.assertIn("sudo /opt/af/af_runner.sh add-repo me/app", self.calls())
        self.assertNotIn(TOKEN, "\n".join(self.calls()) + output, "the token is never an argument or printed")
        self.assertIn("gh api -X POST repos/me/app/actions/runners/registration-token --jq .token", self.calls())
        remote_add = next(call for call in self.calls() if call.startswith("ssh") and "add-repo" in call)
        staged = remote_add.rsplit("< ", 1)[1].split()[0]
        self.assertFalse(os.path.exists(staged), "the staged token file is removed")

    def test_invalid_input_is_refused_before_connecting(self) -> None:
        self.assert_fails(self.pakem("setup", "host", "admin@host", "--deploy-root", "/", "--plan"), code=2)
        self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--runner", "--yes")
        self.assert_fails(self.pakem("host", "runner", "add", "prod", "not a repo", "--yes"), code=2)

    def service_project(self, name: str = "demo"):
        project = self.base / name
        write(project / "deploy" / "release.toml",
              f'project = "{name}"\n\n[environments.staging]\n\n[environments.production]\n')
        write(project / "deploy" / "settings.toml", 'placement = ["HTTP_PORT"]\nsecrets = ["DB_PASSWORD"]\n')
        return project

    def test_one_run_makes_the_key_sets_up_the_host_and_allows_the_projects(self) -> None:
        first, second = self.service_project("demo"), self.service_project("other")
        code, output = self.run_tty("setup", "host", "admin@203.0.113.7", "--name", "prod", "--no-passphrase",
                                    "--project-dir", str(first), "--project-dir", str(second), "--yes")
        self.assertEqual(code, 0, output)
        key = self.home / ".ssh" / "pakem_device_ed25519.pub"
        self.assertTrue(key.is_file(), "the device key is made first")
        calls = self.calls()
        install = next(i for i, call in enumerate(calls) if "af_bootstrap.sh install" in call)
        authorize = [i for i, call in enumerate(calls) if call.startswith("sudo python3 /opt/af/af_receive.py authorize")]
        self.assertEqual(len(authorize), 2)
        self.assertTrue(all(index > install for index in authorize), "projects are allowed after the install")
        line = calls[authorize[0]]
        for part in ("--project demo", "--environment production", "--environment staging", "--placement HTTP_PORT",
                     "--secret DB_PASSWORD", "--switch-from-registry", "--proxy", " ".join(key.read_text().split()[:2])):
            self.assertIn(part, line)
        self.assertEqual(sorted(self.hosts()["prod"]["projects"]), ["demo", "other"])
        self.assertIn("This device may release demo, other there", output)

    def test_without_a_terminal_the_owner_action_is_the_one_command(self) -> None:
        project = self.service_project()
        completed = self.pakem("setup", "host", "admin@203.0.113.7", "--project-dir", str(project), "--yes")
        self.assert_fails(completed, code=3)
        self.assertIn(f"--project-dir {project}", completed.stderr)
        self.assertEqual(self.calls(), [])

    def test_only_merged_main_reaches_a_server(self) -> None:
        git(self.framework, "checkout", "-q", "-b", "feat/topic", home=self.home)
        refused = self.pakem("setup", "host", "admin@203.0.113.7", "--plan")
        self.assert_fails(refused, code=6)
        self.assertIn("pass --allow-unmerged", refused.stderr)
        self.assert_ok(self.pakem("setup", "host", "admin@203.0.113.7", "--plan", "--allow-unmerged"))
