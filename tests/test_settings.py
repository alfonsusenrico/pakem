"""pakem secret, pakem placement, and pakem settings status, against a fake SSH that runs the
real release receiver on a temporary host layout (ADR 0015).

Requests are signed with the device key made per test and verified by the receiver with
the real ssh-keygen. A fake gh records deploy-key registrations, and a fake dialog program
(osascript on macOS, zenity elsewhere) answers from a state file, so no real dialog opens.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pty
import select
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import DEPLOY_ASSETS, PAKEM, REPO_ROOT, IsolatedTestCase, git, init_repo, isolated_env, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(DEPLOY_ASSETS))
import af_receive  # noqa: E402
from pakem_lib import settings  # noqa: E402
from pakem_lib.requirements import device_label  # noqa: E402

SECRET = "typed-secret-never-printed"
FILE_SECRET = "file-secret-never-printed"
FAKE_SSH = """#!{python}
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["-V"]:
    print("OpenSSH_fake", file=sys.stderr)
    sys.exit(0)
index = 0
while index < len(args) and args[index].startswith("-"):
    index += 1 if args[index] == "-t" else 2
target, remote = args[index], " ".join(args[index + 1:])
with open("{log}", "a") as log:
    log.write(f"ssh {{target}} {{remote}}\\n")
state = Path("{state}")
if remote == "settings":
    if (state / "old-receiver").exists():
        print("af-receive: refused: expected `release`, `history <project>`, or `status <project>`")
        sys.exit(2)
    sys.path.insert(0, "{assets}")
    import af_receive
    receiver = af_receive.Receiver("{device}", ["example"], config_dir=Path("{config}"), signers=Path("{signers}"))
    sys.exit(receiver.serve("settings", sys.stdin.buffer))
if remote.startswith("history ") or remote.startswith("status "):
    print('{{"project": "example", "environments": {{}}}}')
if remote == "release":
    sys.stdin.buffer.read()
    print("af-receive: result deployed")
sys.exit(0)
"""
FAKE_GH = """#!/bin/sh
case "$1 $2" in
  "auth status") echo "Logged in to github.com" ;;
  "repo deploy-key")
    echo "gh $*" >> "{log}"
    cp "$4" "{state}/registered-$(ls "{state}" | grep -c '^registered-' || true).pub"
    exit "$(cat "{state}/gh-exit" 2>/dev/null || echo 0)" ;;
  "api --paginate") printf 'CI\\tcompleted\\tsuccess\\n' ;;
  "api repos/"*) ;;
  *) echo "gh version 2.0" ;;
esac
"""
# The dialog prints the value file's content, or acts as cancelled or as a desktop that
# refuses dialogs; it logs only its arguments (the name and description, never values).
FAKE_DIALOG = """#!/bin/sh
printf '%s\\n' "dialog $*" >> "{log}"
[ -e "{state}/dialog-unavailable" ] && {{ echo "execution error: No user interaction allowed. (-1713)" >&2; exit 255; }}
[ -e "{state}/dialog-cancel" ] && {{ echo "execution error: User canceled. (-128)" >&2; exit 1; }}
cat "{state}/dialog-answer"
echo
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
SETTINGS_TOML = """af_standard = 1
required = ["PUBLIC_HOST", "API_TOKEN", "SESSION_TOKEN"]
optional = ["HTTP_PORT", "REPO_KEY", "PUSH_KEY", "MAIL_PASSWORD"]
placement = ["PUBLIC_HOST", "HTTP_PORT"]
secrets = ["API_TOKEN", "MAIL_PASSWORD", "SESSION_TOKEN", "REPO_KEY", "PUSH_KEY"]
host_generated = ["COOKIE_KEY"]

[generators]
COOKIE_KEY = "token_urlsafe:32"

[descriptions]
PUBLIC_HOST = "The public domain"
API_TOKEN = "The provider's API token, from its dashboard"

[generate]
SESSION_TOKEN = "token:32"
REPO_KEY = "deploy-key:read"
PUSH_KEY = "deploy-key:write"
"""
HOSTS_TOML = """[hosts.prod]
target = "admin@203.0.113.7"
deploy_root = "/srv/deploy"
trigger = "pull"
proxy = true
runner = false
projects = ["example"]
"""


class SettingsTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bin = self.base / "bin"
        self.state = self.base / "fake-state"
        self.state.mkdir()
        self.log = self.base / "calls.log"
        self.log.write_text("")
        key = self.home / ".ssh" / "pakem_device_ed25519"
        key.parent.mkdir(mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "device", "-f", str(key)], check=True)
        self.host_root = self.base / "host"
        self.config_dir = self.host_root / "hosts"
        self.config_dir.mkdir(parents=True)
        self.deploy_root = self.host_root / "deploy"
        self.deploy_root.mkdir()
        files = af_receive.KeyFiles(self.host_root / "authorized_keys", self.host_root / "allowed_signers")
        authorize = argparse.Namespace(
            device=device_label(), key=" ".join(key.with_suffix(".pub").read_text().split()[:2]), project="example",
            environment=["staging", "production"], deploy_root=str(self.deploy_root), proxy=False,
            switch_from_registry=False, placement=[], secret=[],
        )
        with redirect_stdout(io.StringIO()):
            af_receive.authorize(authorize, files, self.config_dir)
        self.dialog_name = "osascript" if sys.platform == "darwin" else "zenity"
        fakes = {"ssh": FAKE_SSH, "gh": FAKE_GH, self.dialog_name: FAKE_DIALOG}
        for name, script in fakes.items():
            text = script.format(python=sys.executable, log=self.log, state=self.state, assets=DEPLOY_ASSETS,
                                 device=device_label(), config=self.config_dir, signers=files.signers)
            write(self.bin / name, text, mode=0o755)
        self.env = isolated_env(self.home, {"PATH": f"{self.bin}:/usr/bin:/bin", "DISPLAY": ":0"})
        write(self.home / ".config" / "pakem" / "hosts.toml", HOSTS_TOML)
        self.project = self.make_project()

    def make_project(self) -> Path:
        project = init_repo(self.base / "example", self.home)
        write(project / "deploy" / "release.toml", RELEASE_TOML)
        write(project / "deploy" / "settings.toml", SETTINGS_TOML)
        write(project / "compose.yaml", "services: {}\n")
        git(project, "add", "-A", home=self.home)
        git(project, "commit", "-q", "-m", "feat: service", home=self.home)
        git(project, "remote", "add", "origin", "https://github.com/me/example.git", home=self.home)
        return project

    def pakem_in_project(self, *args: str, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PAKEM), "--root", str(self.framework), *args],
            capture_output=True, text=True, env=self.env, stdin=stdin, cwd=self.project,
        )

    def base_dir(self, environment: str = "staging") -> Path:
        return self.deploy_root / "example" / environment

    def secret(self, name: str, environment: str = "staging") -> Path:
        return self.base_dir(environment) / "secrets" / name

    def calls(self, prefix: str = "") -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def assert_never_shown(self, completed: subprocess.CompletedProcess, *values: str) -> None:
        for value in (SECRET, FILE_SECRET, *values):
            self.assertNotIn(value, completed.stdout)
            self.assertNotIn(value, completed.stderr)
            self.assertNotIn(value, self.log.read_text(), "values never reach arguments")


class SecretSetTests(SettingsTestCase):
    def test_a_secret_from_a_file_reaches_the_host(self) -> None:
        value_file = write(self.base / "value.txt", FILE_SECRET + "\n")
        completed = self.pakem_in_project("secret", "set", "API_TOKEN", "--from-file", str(value_file), "--yes")
        self.assert_ok(completed)
        self.assertIn("API_TOKEN: stored", completed.stdout)
        self.assertEqual(self.secret("API_TOKEN").read_text(), FILE_SECRET)
        self.assertEqual(self.calls("ssh"), ["ssh af@203.0.113.7 settings"])
        self.assert_never_shown(completed)

    def test_undeclared_names_and_other_kinds_are_refused_before_sending(self) -> None:
        value_file = write(self.base / "value.txt", FILE_SECRET)
        for args, message in (
            (("secret", "set", "UNKNOWN", "--from-file", str(value_file)), "UNKNOWN is not declared"),
            (("secret", "set", "PUBLIC_HOST", "--from-file", str(value_file)), "Set it with `pakem placement set`"),
            (("secret", "set", "COOKIE_KEY", "--generate", "token:32"), "the engine creates it at deploy"),
            (("secret", "set", "REPO_KEY", "--generate", "token:32"), "generates REPO_KEY as deploy-key:read"),
            (("placement", "set", f"API_TOKEN={SECRET}"), "API_TOKEN is a secret"),
            (("placement", "set", f"UNKNOWN={SECRET}"), "UNKNOWN is not declared"),
            (("secret", "set", "API_TOKEN", "--from-file", str(value_file), "--environment", "production"),
             "Pass --production"),
        ):
            with self.subTest(args=args):
                completed = self.pakem_in_project(*args, "--yes")
                self.assert_fails(completed, code=6)
                self.assertIn(message, completed.stderr)
                self.assert_never_shown(completed)
        self.assertEqual(self.calls(), [])

    def test_plan_makes_no_calls(self) -> None:
        value_file = write(self.base / "value.txt", FILE_SECRET)
        for args in (
            ("secret", "set", "API_TOKEN", "--from-file", str(value_file)),
            ("secret", "set", "--generate-missing"),
            ("secret", "set", "--missing", "--dialog"),
            ("secret", "set", "REPO_KEY", "--generate", "deploy-key:read"),
            ("placement", "set", "PUBLIC_HOST=example.test"),
        ):
            with self.subTest(args=args):
                completed = self.pakem_in_project(*args, "--plan")
                self.assert_ok(completed)
                self.assertIn("Plan only: nothing changed.", completed.stdout)
                if "deploy-key:read" in args:
                    self.assertIn("register REPO_KEY's public key on me/example as deploy key "
                                  "example-staging-REPO_KEY (read-only)", completed.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.secret("API_TOKEN").exists())

    def test_generate_missing_creates_tokens_and_registers_deploy_keys(self) -> None:
        completed = self.pakem_in_project("secret", "set", "--generate-missing", "--yes")
        self.assert_ok(completed)
        for line in ("SESSION_TOKEN: generated (token:32)", "REPO_KEY: generated (ed25519 deploy key, read)",
                     "REPO_KEY: registered (me/example, read-only)", "PUSH_KEY: registered (me/example, with write access)"):
            self.assertIn(line, completed.stdout)
        self.assertIn("Not generated (pakem cannot create them): API_TOKEN, MAIL_PASSWORD", completed.stdout)
        registrations = self.calls("gh")
        self.assertEqual(len(registrations), 2)
        read, write_access = registrations
        self.assertRegex(read, r"^gh repo deploy-key add \S+/repo_key\.pub --repo me/example --title example-staging-REPO_KEY$")
        self.assertTrue(write_access.endswith("--repo me/example --title example-staging-PUSH_KEY --allow-write"))
        self.assertFalse(Path(read.split()[4]).exists(), "the key file is removed")
        registered = (self.state / "registered-0.pub").read_text().split()
        stored = self.secret("REPO_KEY")
        stored_copy = write(self.base / "stored-key", stored.read_text(), mode=0o600)
        derived = subprocess.run(["ssh-keygen", "-y", "-f", str(stored_copy)], capture_output=True, text=True, check=True)
        self.assertEqual(registered[:2], derived.stdout.split()[:2])
        self.assertNotIn("PRIVATE KEY", completed.stdout + completed.stderr)
        self.assertEqual(len(self.secret("SESSION_TOKEN").read_text()), 43)
        self.assertNotIn(self.secret("SESSION_TOKEN").read_text(), completed.stdout + completed.stderr)
        again = self.pakem_in_project("secret", "set", "--generate-missing", "--yes")
        self.assert_ok(again)
        self.assertEqual(len(self.calls("gh")), 2, "a second run generates nothing")

    def test_a_failed_registration_names_the_public_key_and_the_title(self) -> None:
        (self.state / "gh-exit").write_text("1")
        completed = self.pakem_in_project("secret", "set", "REPO_KEY", "--generate", "deploy-key:read", "--yes")
        self.assert_fails(completed, code=5)
        self.assertIn("REPO_KEY is stored on prod, but gh could not register", completed.stderr)
        self.assertIn("Add it as deploy key example-staging-REPO_KEY: ssh-ed25519 ", completed.stderr)
        self.assertNotIn("PRIVATE", completed.stderr)

    def test_missing_with_a_dialog_stores_each_answer(self) -> None:
        (self.state / "dialog-answer").write_text(SECRET)
        completed = self.pakem_in_project("secret", "set", "--missing", "--dialog", "--yes")
        self.assert_ok(completed)
        self.assertIn("API_TOKEN: stored", completed.stdout)
        self.assertIn("MAIL_PASSWORD: stored", completed.stdout)
        self.assertIn("SESSION_TOKEN: skipped (pakem creates it", completed.stdout)
        self.assertEqual(self.secret("API_TOKEN").read_text(), SECRET)
        dialogs = self.calls("dialog")
        self.assertEqual(len(dialogs), 2)
        self.assertIn("API_TOKEN", dialogs[0])
        self.assertIn("The provider's API token, from its dashboard", dialogs[0])
        self.assert_never_shown(completed)

    def test_a_cancelled_dialog_skips_the_secret_and_names_the_owner_action(self) -> None:
        (self.state / "dialog-cancel").touch()
        completed = self.pakem_in_project("secret", "set", "--missing", "--dialog", "--yes", "--json")
        self.assert_fails(completed, code=3)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "owner-action")
        self.assertEqual(result["settings"]["API_TOKEN"], "cancelled")
        self.assertEqual(result["owner_actions"], [
            "OWNER ACTION REQUIRED: run `pakem secret set --missing` in your terminal "
            "(API_TOKEN, MAIL_PASSWORD still missing)"])
        self.assertFalse(self.secret("API_TOKEN").exists())

    def test_without_a_desktop_or_terminal_the_owner_runs_one_command(self) -> None:
        (self.state / "dialog-unavailable").touch()
        for extra in ((), ("--dialog",)):
            with self.subTest(extra=extra):
                completed = self.pakem_in_project("secret", "set", "--missing", *extra, "--yes")
                self.assert_fails(completed, code=3)
                self.assertIn("OWNER ACTION REQUIRED: run `pakem secret set --missing` in your terminal",
                              completed.stdout)
        single = self.pakem_in_project("secret", "set", "API_TOKEN", "--environment", "production", "--production",
                                       "--yes")
        self.assert_fails(single, code=3)
        self.assertIn("run `pakem secret set API_TOKEN --environment production --production` in your terminal",
                      single.stderr)
        self.assertFalse(self.secret("API_TOKEN").exists())

    def run_tty(self, *args: str, answer: str) -> tuple[int, str]:
        """Run pakem on a pseudo-terminal and type the answer at the hidden prompt."""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.project)
            os.execve(sys.executable, [sys.executable, str(PAKEM), "--root", str(self.framework), *args], self.env)
        output, typed, deadline = b"", False, time.monotonic() + 30
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
            if not typed and b"hidden: " in output:
                os.write(fd, answer.encode() + b"\n")
                typed = True
        else:
            os.kill(pid, 9)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), output.decode(errors="replace")

    def test_hidden_terminal_input_is_never_echoed(self) -> None:
        code, output = self.run_tty("secret", "set", "API_TOKEN", "--yes", answer=SECRET)
        self.assertEqual(code, 0, output)
        self.assertIn("API_TOKEN (The provider's API token, from its dashboard), hidden: ", output)
        self.assertIn("API_TOKEN: stored", output)
        self.assertNotIn(SECRET, output)
        self.assertEqual(self.secret("API_TOKEN").read_text(), SECRET)

    def test_a_host_without_the_settings_receiver_names_setup_host(self) -> None:
        (self.state / "old-receiver").touch()
        completed = self.pakem_in_project("secret", "list")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup host prod` in your terminal once", completed.stderr)


class ReadAndPlacementTests(SettingsTestCase):
    def test_placement_set_merges_and_list_shows_values_only_when_asked(self) -> None:
        completed = self.pakem_in_project("placement", "set", "PUBLIC_HOST=example.test", "HTTP_PORT=18080", "--yes")
        self.assert_ok(completed)
        self.assertIn("PUBLIC_HOST: stored", completed.stdout)
        self.assertEqual((self.base_dir() / "placement.env").read_text(), "PUBLIC_HOST=example.test\nHTTP_PORT=18080\n")
        self.assert_ok(self.pakem_in_project("placement", "set", "HTTP_PORT=18090", "--yes"))
        self.assertEqual((self.base_dir() / "placement.env").read_text(), "PUBLIC_HOST=example.test\nHTTP_PORT=18090\n")
        listed = self.pakem_in_project("placement", "list")
        self.assert_ok(listed)
        self.assertNotIn("example.test", listed.stdout)
        self.assertRegex(listed.stdout, r"PUBLIC_HOST\s+set")
        with_values = json.loads(self.pakem_in_project("placement", "list", "--values", "--json").stdout)
        self.assertEqual(with_values["placement"][0], {"name": "PUBLIC_HOST", "state": "set", "value": "example.test"})

    def test_secret_list_and_settings_status_never_show_values(self) -> None:
        value_file = write(self.base / "value.txt", FILE_SECRET)
        self.assert_ok(self.pakem_in_project("secret", "set", "API_TOKEN", "--from-file", str(value_file), "--yes"))
        listed = self.pakem_in_project("secret", "list")
        self.assert_ok(listed)
        self.assertRegex(listed.stdout, r"API_TOKEN\s+set, just set")
        self.assertRegex(listed.stdout, r"MAIL_PASSWORD\s+missing")
        self.assertRegex(listed.stdout, r"COOKIE_KEY\s+missing \(host_generated\)")
        status = self.pakem_in_project("settings", "status", "--json")
        self.assert_ok(status)
        document = json.loads(status.stdout)
        rows = {row["name"]: row for row in document["settings"]}
        self.assertEqual(rows["API_TOKEN"]["state"], "set")
        self.assertEqual(rows["SESSION_TOKEN"]["generate"], "token:32")
        self.assertEqual(rows["PUBLIC_HOST"]["description"], "The public domain")
        self.assertEqual(document["checks"][0]["detail"], "missing: PUBLIC_HOST, SESSION_TOKEN")
        text = self.pakem_in_project("settings", "status")
        self.assertIn("WARNING: required settings missing in example staging on prod: PUBLIC_HOST, SESSION_TOKEN",
                      text.stdout)
        self.assertIn("WARNING: no [descriptions] entry in deploy/settings.toml for: HTTP_PORT, MAIL_PASSWORD, "
                      "SESSION_TOKEN, REPO_KEY, PUSH_KEY", text.stdout)
        self.assert_never_shown(status)
        self.assert_never_shown(text)


class HostChoiceTests(SettingsTestCase):
    def test_the_host_must_be_known_and_unambiguous(self) -> None:
        hosts = self.home / ".config" / "pakem" / "hosts.toml"
        write(hosts, HOSTS_TOML.replace('projects = ["example"]', f'project_dirs = ["{self.project.resolve()}"]'))
        completed = self.pakem_in_project("secret", "list")
        self.assert_fails(completed, code=3)
        self.assertIn("OWNER ACTION REQUIRED: run `pakem setup host prod` in your terminal, so this device may "
                      "release example there", completed.stderr)
        write(hosts, HOSTS_TOML.replace('projects = ["example"]', ""))
        completed = self.pakem_in_project("secret", "list")
        self.assert_fails(completed, code=3)
        self.assertIn("this device may not release example anywhere yet", completed.stderr)
        write(hosts, HOSTS_TOML + HOSTS_TOML.replace("[hosts.prod]", "[hosts.other]"))
        completed = self.pakem_in_project("secret", "list")
        self.assert_fails(completed, code=2)
        self.assertIn("example is recorded for several hosts", completed.stderr)
        self.assertEqual(self.calls(), [])


class InputChoiceTests(unittest.TestCase):
    """Which input a secret comes from: dialog, then hidden terminal input, then the owner."""

    def asker(self, dialog: bool, program: str | None, terminal: bool) -> tuple[settings.Asker, list]:
        patches = [mock.patch.object(settings, "dialog_program", return_value=program),
                   mock.patch.object(settings, "has_terminal", return_value=terminal)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return settings.Asker(dialog, "example staging"), patches

    def completed(self, code: int, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def test_a_dialog_comes_first_when_asked_for(self) -> None:
        asker, _ = self.asker(True, "osascript", True)
        with mock.patch.object(settings.subprocess, "run", return_value=self.completed(0, SECRET.encode() + b"\n")) as run, \
                mock.patch.object(settings.getpass, "getpass") as typed:
            answer = asker.ask("API_TOKEN", "The provider's token")
        self.assertEqual((answer.outcome, answer.value), (settings.VALUE, SECRET.encode()))
        typed.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[0], "osascript")
        self.assertEqual(command[-2], "API_TOKEN")
        self.assertIn("The provider's token", command[-1])
        self.assertFalse(any("The provider" in part for part in command[:-1]), "text is an argument, not script")
        self.assertNotIn(SECRET, repr(answer))

    def test_a_desktop_that_refuses_dialogs_falls_back_to_the_terminal(self) -> None:
        asker, _ = self.asker(True, "osascript", True)
        refused = self.completed(1, stderr=b"execution error: No user interaction allowed. (-1713)")
        with mock.patch.object(settings.subprocess, "run", return_value=refused), \
                mock.patch.object(settings.getpass, "getpass", return_value=SECRET) as typed:
            answer = asker.ask("API_TOKEN", "")
            second = asker.ask("MAIL_PASSWORD", "")
        self.assertEqual(answer.outcome, settings.VALUE)
        self.assertEqual(second.outcome, settings.VALUE)
        self.assertEqual(typed.call_count, 2)

    def test_without_dialog_or_terminal_nothing_is_read(self) -> None:
        for dialog, program in ((True, None), (False, "osascript")):
            with self.subTest(dialog=dialog, program=program):
                asker, patches = self.asker(dialog, program, False)
                with mock.patch.object(settings.subprocess, "run") as run, \
                        mock.patch.object(settings.getpass, "getpass") as typed:
                    self.assertEqual(asker.ask("API_TOKEN", "").outcome, settings.UNAVAILABLE)
                run.assert_not_called()
                typed.assert_not_called()
                for patch in patches:
                    patch.stop()

    def test_the_terminal_is_used_without_dialog(self) -> None:
        asker, _ = self.asker(False, "osascript", True)
        with mock.patch.object(settings.subprocess, "run") as run, \
                mock.patch.object(settings.getpass, "getpass", side_effect=["", EOFError]) as typed:
            self.assertEqual(asker.ask("API_TOKEN", "the token").outcome, settings.SKIPPED)
            self.assertEqual(asker.ask("API_TOKEN", "the token").outcome, settings.CANCELLED)
        run.assert_not_called()
        self.assertEqual(typed.call_args.args[0], "API_TOKEN (the token), hidden: ")

    def test_dialog_outcomes_per_program(self) -> None:
        for program, result, outcome in (
            ("osascript", self.completed(1, stderr=b"execution error: User canceled. (-128)"), settings.CANCELLED),
            ("osascript", self.completed(0, b"\n"), settings.SKIPPED),
            ("zenity", self.completed(1), settings.CANCELLED),
            ("zenity", self.completed(5), settings.SKIPPED),
            ("zenity", self.completed(255), settings.UNAVAILABLE),
            ("kdialog", self.completed(1), settings.CANCELLED),
            ("kdialog", self.completed(0, b"value\n"), settings.VALUE),
        ):
            with self.subTest(program=program, code=result.returncode):
                with mock.patch.object(settings.subprocess, "run", return_value=result):
                    self.assertEqual(settings.ask_dialog(settings.dialog_command(program, "NAME", "text")).outcome,
                                     outcome)
        with mock.patch.object(settings.subprocess, "run", side_effect=subprocess.TimeoutExpired("osascript", 1)):
            self.assertEqual(settings.ask_dialog(["osascript"]).outcome, settings.SKIPPED)

    def test_linux_dialogs_take_the_name_and_description(self) -> None:
        self.assertEqual(settings.dialog_command("zenity", "API_TOKEN", "the token"),
                         ["zenity", "--entry", "--hide-text", "--title", "API_TOKEN", "--text", "the token"])
        self.assertEqual(settings.dialog_command("kdialog", "API_TOKEN", "the token"),
                         ["kdialog", "--title", "API_TOKEN", "--password", "the token"])
        with mock.patch.object(settings.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            self.assertIsNone(settings.dialog_program())


if __name__ == "__main__":
    unittest.main()
