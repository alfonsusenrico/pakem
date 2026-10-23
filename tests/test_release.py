"""pakem release and pakem host allow-device|revoke-device, against a fake host and forge.

The fake ssh plays the host: it answers `history`/`status` from a file, keeps what a
`release` sends, and logs admin commands. The fake gh answers check runs and statuses.
Requests are verified with the real ssh-keygen against the device key made per test.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import pty
import select
import subprocess
import sys
import tarfile
import time
import tomllib
import unittest
from unittest import mock

from support import PAKEM, REPO_ROOT, IsolatedTestCase, git, init_repo, isolated_env, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
from pakem_lib.host import HostRecord, load_hosts, save_host  # noqa: E402
from pakem_lib.release import previous_commit  # noqa: E402

FAKE_SSH = """#!/bin/sh
[ "$1" = -V ] && { echo "OpenSSH_fake" >&2; exit 0; }
while [ $# -gt 0 ]; do
  case "$1" in
    -i|-o|-p|-O) shift 2 ;;
    -t) shift ;;
    *) break ;;
  esac
done
target="$1"; shift
remote="$*"
echo "ssh $target $remote" >> "{log}"
case "$remote" in
  "history "*|"status "*) cat "{state}/history.json" ;;
  release)
    n=$(ls "{state}" | grep -c '^payload-' || true)
    cat > "{state}/payload-$n"
    echo "af-receive: result deployed"
    exit "$(cat "{state}/release-exit" 2>/dev/null || echo 0)" ;;
  "test -f "*) [ -e "{state}/no-receiver" ] && exit 1; exit 0 ;;
  sudo*) exit 0 ;;
esac
"""
FAKE_GH = """#!/bin/sh
case "$1 $2" in
  "auth status") echo "Logged in to github.com" ;;
  "api --paginate") cat "{state}/check-runs" ;;
  "api repos/"*) cat "{state}/statuses" ;;
  *) echo "gh version 2.0" ;;
esac
"""
RELEASE_TOML = """af_standard = 1
project = "example"
compose_files = ["compose.yaml"]

[environments.staging]
keep = 3

[environments.production]
keep = 6
backup = "required"
"""
SETTINGS_TOML = 'af_standard = 1\nplacement = ["PUBLIC_HOST"]\nsecrets = ["DATABASE_PASSWORD"]\n'
HOSTS_TOML = """[hosts.prod]
target = "admin@203.0.113.7"
deploy_root = "/srv/deploy"
trigger = "pull"
proxy = true
runner = false
projects = ["example"]
"""


def deployment(commit: str, result: str = "deployed", version: str | None = None, trigger: str = "direct") -> dict:
    return {"commit": commit, "result": result, "version": version, "finished": "2026-10-22T00:00:00Z",
            "actor": "device:laptop", "trigger": trigger}


def rolled_back_to(commit: str) -> dict:
    return deployment(commit, trigger="rollback")


class ReleaseTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bin = self.base / "bin"
        self.state = self.base / "fake-state"
        self.state.mkdir()
        self.log = self.base / "calls.log"
        self.log.write_text("")
        for name, script in (("ssh", FAKE_SSH), ("gh", FAKE_GH)):
            text = script.replace("{log}", str(self.log)).replace("{state}", str(self.state))
            write(self.bin / name, text, mode=0o755)
        self.env = isolated_env(self.home, {"PATH": f"{self.bin}:/usr/bin:/bin"})
        key = self.home / ".ssh" / "pakem_device_ed25519"
        key.parent.mkdir(mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "device", "-f", str(key)], check=True)
        self.public_key = key.with_suffix(".pub").read_text()
        write(self.home / ".config" / "pakem" / "hosts.toml", HOSTS_TOML)
        self.project = self.make_project()
        self.main = git(self.project, "rev-parse", "HEAD", home=self.home).stdout.strip()
        self.checks("CI\tcompleted\tsuccess\n")
        self.history({})

    def make_project(self):
        project = init_repo(self.base / "example", self.home)
        write(project / "deploy" / "release.toml", RELEASE_TOML)
        write(project / "deploy" / "settings.toml", SETTINGS_TOML)
        write(project / "compose.yaml", "services: {}\n")
        git(project, "add", "-A", home=self.home)
        git(project, "commit", "-q", "-m", "feat: service", home=self.home)
        origin = self.base / "origin.git"
        git(self.base, "init", "-q", "--bare", str(origin), home=self.home)
        url = "https://github.com/me/example.git"
        git(project, "remote", "add", "origin", url, home=self.home)
        git(project, "config", f"url.{origin}.insteadOf", url, home=self.home)
        git(project, "push", "-q", "origin", "main", home=self.home)
        return project

    def checks(self, runs: str, statuses: str = "") -> None:
        (self.state / "check-runs").write_text(runs)
        (self.state / "statuses").write_text(statuses)

    def history(self, environments: dict) -> None:
        (self.state / "history.json").write_text(json.dumps({"project": "example", "environments": environments}))

    def release(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "release", *args,
             "--project-dir", str(self.project)],
            capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL,
        )

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line]

    def payloads(self) -> list[tuple[dict, bytes]]:
        """Each request the fake host received, verified against the device key, and its archive."""
        received = []
        for path in sorted(self.state.glob("payload-*")):
            request_line, signature_line, archive = path.read_bytes().split(b"\n", 2)
            signers = self.base / "allowed_signers"
            signers.write_text(f'example namespaces="pakem-release" {self.public_key}')
            signature = self.base / "request.sig"
            signature.write_bytes(base64.b64decode(signature_line))
            verified = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", "example", "-n", "pakem-release",
                 "-s", str(signature)], input=request_line, capture_output=True,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            received.append((json.loads(request_line), archive))
        return received


class PublishTests(ReleaseTestCase):
    def test_publish_signs_and_sends_main_with_its_archive(self) -> None:
        completed = self.release("publish", "--yes")
        self.assert_ok(completed)
        self.assertIn(f"Released example {self.main[:12]} to staging on prod.", completed.stdout)
        [(request, archive)] = self.payloads()
        self.assertEqual(
            {key: request[key] for key in ("action", "project", "environment", "commit", "version")},
            {"action": "publish", "project": "example", "environment": "staging", "commit": self.main, "version": None},
        )
        self.assertEqual(request["archive"], f"sha256:{hashlib.sha256(archive).hexdigest()}")
        self.assertEqual(request["source"], "https://github.com/me/example")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertIn("deploy/release.toml", tar.getnames())
        self.assertIn("ssh af@203.0.113.7 release", self.calls())

    def test_a_kept_commit_is_sent_without_its_archive(self) -> None:
        self.history({"staging": {"running": None, "kept": [self.main], "deployments": []}})
        self.assert_ok(self.release("publish", "--yes"))
        [(request, archive)] = self.payloads()
        self.assertIsNone(request["archive"])
        self.assertEqual(archive, b"")

    def test_checks_must_pass_before_anything_is_sent(self) -> None:
        for runs, statuses, message in (
            ("CI\tcompleted\tfailure\n", "", "CI (failure)"),
            ("CI\tin_progress\t\n", "", "CI (in_progress)"),
            ("", "", "has no checks"),
            ("CI\tcompleted\tsuccess\n", "deploy/preview\tpending\n", "deploy/preview (pending)"),
        ):
            with self.subTest(message=message):
                self.checks(runs, statuses)
                completed = self.release("publish", "--yes")
                self.assert_fails(completed, code=5)
                self.assertIn(message, completed.stderr)
        self.assertEqual(self.payloads(), [])

    def test_a_device_not_allowed_anywhere_gets_the_owner_action(self) -> None:
        write(self.home / ".config" / "pakem" / "hosts.toml", HOSTS_TOML.replace('projects = ["example"]', ""))
        completed = self.release("publish", "--yes")
        self.assert_fails(completed, code=3)
        self.assertIn(f"record it with `pakem host add prod --project-dir {self.project.resolve()}`", completed.stderr)
        self.assertIn("then OWNER ACTION REQUIRED: run `pakem setup host prod` in your terminal", completed.stderr)

    def test_host_results_map_to_pakem_exit_codes(self) -> None:
        for host_exit, code in (("2", 6), ("3", 5), ("5", 5), ("75", 75)):
            with self.subTest(host_exit=host_exit):
                (self.state / "release-exit").write_text(host_exit)
                self.assert_fails(self.release("publish", "--yes"), code=code)

    def test_projects_released_by_others_are_refused(self) -> None:
        write(self.project / "AGENTS.md", "# Example\n\nDelivery: others (the client's team deploys)\n")
        completed = self.release("publish", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("Delivery: others", completed.stderr)

    def test_without_staging_publish_points_to_promote(self) -> None:
        write(self.project / "deploy" / "release.toml", RELEASE_TOML.split("[environments.staging]")[0]
              + "[environments.production]\nkeep = 6\n")
        completed = self.release("publish", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("release promote vX.Y.Z --production", completed.stderr)

    def test_plan_sends_nothing(self) -> None:
        completed = self.release("publish", "--plan")
        self.assert_ok(completed)
        self.assertIn("checks passed: CI", completed.stdout)
        self.assertEqual(self.payloads(), [])


class PromoteTests(ReleaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.history({"staging": {"running": self.main, "kept": [self.main], "deployments": [deployment(self.main)]},
                      "production": {"running": None, "kept": [], "deployments": []}})

    def test_production_needs_the_flag(self) -> None:
        completed = self.release("promote", "v1.0.0", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("pass --production", completed.stderr)

    def test_promote_sends_what_staging_runs_then_tags_it(self) -> None:
        completed = self.release("promote", "v1.0.0", "--production", "--yes")
        self.assert_ok(completed)
        [(request, archive)] = self.payloads()
        self.assertEqual((request["action"], request["environment"], request["version"]), ("promote", "production", "v1.0.0"))
        self.assertEqual((request["commit"], request["archive"], archive), (self.main, None, b""))
        tagged = git(self.base / "origin.git", "rev-parse", "v1.0.0^{commit}", home=self.home)
        self.assertEqual(tagged.stdout.strip(), self.main)

    def test_a_failed_deployment_is_not_tagged(self) -> None:
        (self.state / "release-exit").write_text("3")
        self.assert_fails(self.release("promote", "v1.0.0", "--production", "--yes"), code=5)
        self.assertNotEqual(git(self.project, "tag", "--list", home=self.home).stdout.strip(), "v1.0.0")

    def test_versions_only_go_forward(self) -> None:
        git(self.project, "tag", "v2.0.0", home=self.home)
        git(self.project, "push", "-q", "origin", "v2.0.0", home=self.home)
        git(self.project, "commit", "-q", "--allow-empty", "-m", "fix: later", home=self.home)
        git(self.project, "push", "-q", "origin", "main", home=self.home)
        completed = self.release("promote", "v1.5.0", "--production", "--yes", "--commit", "HEAD")
        self.assert_fails(completed, code=6)
        self.assertIn("not newer than the newest release tag v2.0.0", completed.stderr)
        completed = self.release("promote", "v2.0.0", "--production", "--yes", "--commit", "HEAD")
        self.assert_fails(completed, code=6)
        self.assertIn("tag v2.0.0 already names", completed.stderr)


class RollbackTests(ReleaseTestCase):
    def test_previous_steps_back_past_rollbacks_and_failures(self) -> None:
        a, b, c, d = "a" * 40, "b" * 40, "c" * 40, "d" * 40
        history = [deployment(a), deployment(b), deployment(d, "restored"), deployment(c)]
        self.assertEqual(previous_commit(history, c), b)
        history.append(rolled_back_to(b))
        self.assertEqual(previous_commit(history, b), a)
        history.append(rolled_back_to(a))
        self.assertIsNone(previous_commit(history, a))
        self.assertIsNone(previous_commit(history, d), "a release that never deployed has no previous")

    def test_previous_never_lands_on_a_release_rolled_back_from(self) -> None:
        a, b, c = "a" * 40, "b" * 40, "c" * 40
        history = [deployment(a), deployment(b), rolled_back_to(a), deployment(c)]
        self.assertEqual(previous_commit(history, c), a)
        history.append(rolled_back_to(a))
        self.assertIsNone(previous_commit(history, a), "b and c were both left by rollbacks")

    def test_a_commit_promoted_again_is_one_live_release(self) -> None:
        a, b = "a" * 40, "b" * 40
        history = [deployment(a, version="v1.0.0"), deployment(b, version="v1.1.0"),
                   deployment(b, version="v1.1.1")]
        self.assertEqual(previous_commit(history, b), a)

    def test_an_older_commit_promoted_again_keeps_the_release_it_replaced(self) -> None:
        a, b = "a" * 40, "b" * 40
        history = [deployment(a, version="v1.0.0"), deployment(b, version="v1.1.0"),
                   deployment(a, version="v1.2.0")]
        self.assertEqual(previous_commit(history, a), b, "a promotion, not a rollback, leaves b a working release")

    def test_rollback_previous_sends_the_kept_release(self) -> None:
        older = "a" * 40
        self.history({"staging": {"running": self.main, "kept": [older, self.main],
                                  "deployments": [deployment(older), deployment(self.main)]}})
        completed = self.release("rollback", "previous", "--yes")
        self.assert_ok(completed)
        [(request, archive)] = self.payloads()
        self.assertEqual((request["action"], request["commit"], request["archive"]), ("rollback", older, None))
        self.assertFalse(request["allow_schema"])

    def test_an_older_receiver_reports_running_through_its_deployment_record(self) -> None:
        older = self.main
        git(self.project, "commit", "-q", "--allow-empty", "-m", "feat: newer", home=self.home)
        newer = git(self.project, "rev-parse", "HEAD", home=self.home).stdout.strip()
        self.history({"staging": {"running": None, "kept": [], "current": deployment(newer),
                                  "deployments": [deployment(older), deployment(newer)]}})
        status = self.release("status")
        self.assertIn(f"staging: running {newer[:12]}", status.stdout)
        self.assert_ok(self.release("rollback", "previous", "--yes"))
        [(request, archive)] = self.payloads()
        self.assertEqual(request["commit"], older)
        self.assertNotEqual(archive, b"", "a release the host does not keep goes with its source")

    def test_rolling_back_production_needs_the_flag_and_names_a_commit(self) -> None:
        self.history({"production": {"running": None, "kept": [], "deployments": []}})
        completed = self.release("rollback", "previous", "--environment", "production", "--yes")
        self.assert_fails(completed, code=6)
        self.assertIn("pass --production", completed.stderr)
        self.assertEqual(self.payloads(), [])

        older = self.main
        git(self.project, "commit", "-q", "--allow-empty", "-m", "feat: newer", home=self.home)
        newer = git(self.project, "rev-parse", "HEAD", home=self.home).stdout.strip()
        git(self.project, "tag", "v1.0.0", older, home=self.home)
        self.history({"production": {"running": newer, "kept": [older, newer],
                                     "deployments": [deployment(older, version="v1.0.0"),
                                                     deployment(newer, version="v1.1.0")]}})
        self.assert_ok(self.release("rollback", "v1.0.0", "--environment", "production", "--production", "--yes"))
        [(request, archive)] = self.payloads()
        self.assertEqual((request["action"], request["environment"], request["commit"]), ("rollback", "production", older))


class AllowDeviceTests(ReleaseTestCase):
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

    def hosts(self) -> dict:
        return tomllib.loads((self.home / ".config" / "pakem" / "hosts.toml").read_text())["hosts"]

    def test_without_a_terminal_nothing_connects(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), "host", "allow-device", "prod",
             "--project-dir", str(self.project), "--yes"],
            capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL,
        )
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: run `pakem host allow-device prod --project-dir", completed.stderr)
        self.assertEqual(self.calls(), [])

    def test_allow_runs_the_receiver_as_root_and_records_the_project(self) -> None:
        write(self.home / ".config" / "pakem" / "hosts.toml", HOSTS_TOML.replace('projects = ["example"]', ""))
        code, output = self.run_tty("host", "allow-device", "prod", "--project-dir", str(self.project), "--yes")
        self.assertEqual(code, 0, output)
        authorize = next(call for call in self.calls() if "af_receive.py authorize" in call)
        self.assertTrue(authorize.startswith("ssh admin@203.0.113.7 sudo python3 /opt/af/af_receive.py authorize"))
        for part in ("--project example", "--deploy-root /srv/deploy", "--environment production",
                     "--environment staging", "--placement PUBLIC_HOST", "--secret DATABASE_PASSWORD", "--proxy",
                     " ".join(self.public_key.split()[:2])):
            self.assertIn(part, authorize)
        self.assertEqual(self.hosts()["prod"]["projects"], ["example"])

    def test_a_shared_builder_gets_its_own_key_per_project(self) -> None:
        write(self.home / ".config" / "pakem" / "hosts.toml", HOSTS_TOML.replace('projects = ["example"]', ""))
        builder = write(self.base / "builder.pub", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuilderKeyForTests ci\n")
        refused = self.run_tty("host", "allow-device", "prod", "--project-dir", str(self.project), "--device", "ci",
                               "--key", str(builder), "--yes")
        self.assertEqual(refused[0], 2, refused[1])
        self.assertIn("is named builder-example", refused[1])
        code, output = self.run_tty("host", "allow-device", "prod", "--project-dir", str(self.project),
                                    "--device", "builder-example", "--key", str(builder), "--yes")
        self.assertEqual(code, 0, output)
        authorize = next(call for call in self.calls() if "af_receive.py authorize" in call)
        self.assertIn("--device builder-example --key 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuilderKeyForTests'", authorize)
        self.assertNotIn(self.public_key.split()[1], authorize, "this device's own key is not sent")
        self.assertNotIn("projects", self.hosts()["prod"], "only this device's own permissions are recorded")

    def test_an_old_engine_is_reported(self) -> None:
        (self.state / "no-receiver").touch()
        code, output = self.run_tty("host", "allow-device", "prod", "--project-dir", str(self.project), "--yes")
        self.assertEqual(code, 5, output)
        self.assertIn("runs an engine without direct delivery", output)
        self.assertFalse([call for call in self.calls() if "authorize" in call])

    def test_revoke_removes_the_device_and_forgets_its_projects(self) -> None:
        code, output = self.run_tty("host", "revoke-device", "prod", "--yes")
        self.assertEqual(code, 0, output)
        revoke = next(call for call in self.calls() if "af_receive.py revoke" in call)
        self.assertIn("--device ", revoke)
        self.assertNotIn("projects", self.hosts()["prod"])

    def test_saving_another_host_keeps_each_hosts_projects(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            save_host(HostRecord("other", "admin@198.51.100.2"))
            self.assertEqual(load_hosts()["prod"].projects, ["example"])
            self.assertEqual(load_hosts()["other"].projects, [])


class HistoryTests(ReleaseTestCase):
    def test_history_prints_each_environment_newest_first(self) -> None:
        older = "a" * 40
        self.history({"staging": {"running": self.main, "kept": [], "current": deployment(self.main),
                                  "deployments": [deployment(older), deployment(self.main, version=None)]}})
        completed = self.release("history")
        self.assert_ok(completed)
        lines = completed.stdout.splitlines()
        self.assertEqual(lines[0], "example on prod")
        self.assertTrue(lines[1].startswith(f"staging: running {self.main[:12]}"))
        self.assertIn(self.main[:12], lines[2])
        self.assertIn(older[:12], lines[3])
        data = json.loads(self.release("status", "--json").stdout)
        self.assertEqual(data["environments"]["staging"]["running"], self.main)


if __name__ == "__main__":
    unittest.main()
