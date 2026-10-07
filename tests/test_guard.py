"""enforcement/guard.py decisions for Claude Code and Antigravity hook payloads."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import GUARD, IsolatedTestCase, git, init_repo, isolated_env

DENY = "deny"
ASK = "ask"
ALLOW = None

SHELL_CASES = [
    # (command, expected decision)
    ("git push origin main", DENY),
    ("git push origin HEAD:main", DENY),
    ("git push origin +main", DENY),
    ("git push origin :main", DENY),
    ("git push origin refs/heads/master", DENY),
    ("git -C . push upstream master", DENY),
    ("cd app && git push origin main", DENY),
    ("git push --all origin", DENY),
    ("git push origin feat/x", ALLOW),
    ("git push -u origin feat/x", ALLOW),
    ("git push origin main-docs", ALLOW),
    ("git push --force origin feat/x", ASK),
    ("git push --force-with-lease origin feat/x", ASK),
    ("git push origin +feat/x", ASK),
    ("git push -f origin main", DENY),
    ("git commit --no-verify -m 'feat: x'", DENY),
    ("git commit -n -m 'feat: x'", DENY),
    ("git commit -anm 'feat: x'", DENY),
    ("git push --no-verify origin feat/x", DENY),
    ("git commit -m 'feat: x'", ALLOW),
    ("git commit -am 'feat: x'", ALLOW),
    ("git commit -m 'feat: x' -m 'body'", DENY),
    ("git commit -m 'feat: x\n\nbody'", DENY),
    ("git commit -m 'feat: x Co-Authored-By: bot'", DENY),
    ("git commit --message='feat: x' --trailer 'Signed-off-by: a'", DENY),
    ("git commit -m \"$(cat <<'EOF'\nfeat: subject only\nEOF\n)\"", ALLOW),
    ("git commit -m \"$(cat <<'EOF'\nfeat: x\n\nGenerated with a tool\nEOF\n)\"", DENY),
    ("cat .env", DENY),
    ("cat ./config/.env.production", DENY),
    ("cat .env.example", ALLOW),
    ("less .env.sample", ALLOW),
    ("grep API_KEY .env.local", DENY),
    ("rg TOKEN --glob '*.ts' src .env", DENY),
    ("source .env", DENY),
    (". ./.env", DENY),
    ("head -n 5 server.pem", DENY),
    ("cat ~/.ssh/id_ed25519", DENY),
    ("cat ~/.ssh/id_ed25519.pub", ALLOW),
    ("cat ~/.ssh/known_hosts", ALLOW),
    ("grep -r Host ~/.ssh", DENY),
    ("cp ~/.codex/auth.json /tmp/x", DENY),
    ("base64 secrets/api_secret.txt", DENY),
    ("cat secrets/README.md", ALLOW),
    ("cat deploy/db_prod.txt", DENY),
    ("scp host:~/.ssh/id_rsa .", DENY),
    ("cat .env*", DENY),
    ("python3 app.py < .env", DENY),
    ('echo "$(cat .env)"', DENY),
    ("echo `cat .env`", DENY),
    ("ls && cat app.runtime.env", DENY),
    ("sudo -u root cat /etc/app/.env", DENY),
    ("bash -c 'cat .env'", DENY),
    ("cat README.md", ALLOW),
    ("cat > notes.txt <<'EOF'\nprintenv and cat .env are examples\nEOF", ALLOW),
    ("printenv", DENY),
    ("printenv HOME", DENY),
    ("env", DENY),
    ("env | grep AWS", DENY),
    ("env -i", DENY),
    ("set", DENY),
    ("export -p", DENY),
    ("declare -x", DENY),
    ("env FOO=1 make test", ALLOW),
    ("FOO=1 env", DENY),
    ("set -euo pipefail", ALLOW),
    ("export FOO=1", ALLOW),
    ("declare -a items", ALLOW),
]


GUARD_HOME: Path | None = None


def setUpModule() -> None:
    global GUARD_HOME
    GUARD_HOME = Path(tempfile.mkdtemp(prefix="af-guard-home-"))


def tearDownModule() -> None:
    if GUARD_HOME is not None:
        shutil.rmtree(GUARD_HOME)


def run_guard(host: str, payload: object, cwd: Path | None = None) -> subprocess.CompletedProcess:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(GUARD), "--host", host],
        input=raw,
        capture_output=True,
        text=True,
        env=isolated_env(GUARD_HOME),
        cwd=str(cwd) if cwd else None,
    )


def claude_decision(completed: subprocess.CompletedProcess):
    assert completed.returncode == 0, completed.stderr
    if not completed.stdout.strip():
        return None
    output = json.loads(completed.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecisionReason"]
    return hook_output["permissionDecision"]


def antigravity_decision(completed: subprocess.CompletedProcess):
    assert completed.returncode == 0, completed.stderr
    if not completed.stdout.strip():
        return None
    output = json.loads(completed.stdout)
    assert output["reason"]
    assert output["decision"] != "allow", "guard must never auto-approve"
    return output["decision"]


class GuardShellTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        # A repo on a topic branch, so bare pushes in the cases resolve to a non-main branch.
        self.repo = init_repo(self.base / "work", self.home, branch="feat/topic")

    def test_claude_bash_cases(self) -> None:
        for command, expected in SHELL_CASES:
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.repo)}
                self.assertEqual(claude_decision(run_guard("claude", payload)), expected)

    def test_antigravity_run_command_cases(self) -> None:
        for command, expected in SHELL_CASES:
            with self.subTest(command=command):
                payload = {
                    "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                    "workspacePaths": [str(self.repo)],
                }
                self.assertEqual(antigravity_decision(run_guard("antigravity", payload)), expected)

    def test_bare_push_uses_current_branch(self) -> None:
        on_topic = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(self.repo)}
        self.assertIsNone(claude_decision(run_guard("claude", on_topic)))
        main_repo = init_repo(self.base / "main-work", self.home, branch="main")
        on_main = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(main_repo)}
        self.assertEqual(claude_decision(run_guard("claude", on_main)), DENY)
        head_on_main = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin HEAD"},
            "cwd": str(main_repo),
        }
        self.assertEqual(claude_decision(run_guard("claude", head_on_main)), DENY)
        outside_repo = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(self.base)}
        self.assertEqual(claude_decision(run_guard("claude", outside_repo)), ASK)


CONFIGURED_PUSH_CASES = [
    # Repository protects exactly development and main.
    ("git push origin development", DENY),
    ("git push origin HEAD:development", DENY),
    ("git push origin HEAD:refs/heads/development", DENY),
    ("git push origin +development", DENY),
    ("git push origin :development", DENY),
    ("git push origin refs/heads/development", DENY),
    ("git push -f origin development", DENY),
    ("git push origin main", DENY),
    ("git push origin master", ALLOW),
    ("git push origin feat/x", ALLOW),
    ("git push origin development-docs", ALLOW),
    ("git push --force origin feat/x", ASK),
    ("git push --all origin", DENY),
]


class GuardProtectedBranchConfigTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = init_repo(self.base / "client", self.home, branch="feat/topic")
        for branch in ("development", "main"):
            git(self.repo, "config", "--add", "agentframework.protectedBranch", branch, home=self.home)

    def claude_push(self, command: str, cwd: Path) -> str | None:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
        return claude_decision(run_guard("claude", payload))

    def test_claude_uses_configured_branches_from_cwd(self) -> None:
        for command, expected in CONFIGURED_PUSH_CASES:
            with self.subTest(command=command):
                self.assertEqual(self.claude_push(command, self.repo), expected)

    def test_antigravity_uses_configured_branches(self) -> None:
        for command, expected in CONFIGURED_PUSH_CASES:
            with self.subTest(command=command, source="workspace"):
                payload = {
                    "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                    "workspacePaths": [str(self.repo)],
                }
                self.assertEqual(antigravity_decision(run_guard("antigravity", payload)), expected)
            with self.subTest(command=command, source="process cwd"):
                payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": command}}}
                decision = antigravity_decision(run_guard("antigravity", payload, cwd=self.repo))
                self.assertEqual(decision, expected)

    def test_reason_names_the_branch(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push origin development"}, "cwd": str(self.repo)}
        output = json.loads(run_guard("claude", payload).stdout)
        self.assertIn("protected branch development", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_bare_push_on_configured_branch(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "development", home=self.home)
        self.assertEqual(self.claude_push("git push", self.repo), DENY)
        self.assertEqual(self.claude_push("git push origin HEAD", self.repo), DENY)

    def test_directory_changes_select_the_repository(self) -> None:
        self.assertEqual(self.claude_push("cd client && git push origin development", self.base), DENY)
        self.assertEqual(self.claude_push(f"git -C {self.repo} push origin development", self.base), DENY)

    def test_falls_back_to_main_and_master_outside_a_repository(self) -> None:
        self.assertEqual(self.claude_push("git push origin main", self.base), DENY)
        self.assertEqual(self.claude_push("git push origin master", self.base), DENY)
        self.assertIsNone(self.claude_push("git push origin development", self.base))
        missing = self.base / "does-not-exist"
        self.assertEqual(self.claude_push("git push origin main", missing), DENY)

    def test_unconfigured_repository_protects_main_and_master_only(self) -> None:
        plain = init_repo(self.base / "plain", self.home, branch="feat/topic")
        self.assertEqual(self.claude_push("git push origin master", plain), DENY)
        self.assertIsNone(self.claude_push("git push origin development", plain))


class GuardFileToolTests(unittest.TestCase):
    def test_claude_file_tools(self) -> None:
        cases = [
            ("Read", {"file_path": "/repo/.env"}, DENY),
            ("Read", {"file_path": "/repo/.env.example"}, ALLOW),
            ("Read", {"file_path": "~/.ssh/id_rsa"}, DENY),
            ("Read", {"file_path": "~/.ssh/config"}, ALLOW),
            ("Read", {"file_path": "/repo/src/app.py"}, ALLOW),
            ("Grep", {"pattern": "Host", "path": "~/.ssh"}, DENY),
            ("Grep", {"pattern": "x", "path": "src"}, ALLOW),
            ("Edit", {"file_path": "/repo/.env.local", "old_string": "a", "new_string": "b"}, DENY),
            ("Write", {"file_path": "/repo/certs/server.key", "content": "x"}, DENY),
            ("Write", {"file_path": "/repo/README.md", "content": "cat .env"}, ALLOW),
            ("NotebookEdit", {"notebook_path": "/repo/credentials.json"}, DENY),
        ]
        for tool, tool_input, expected in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                payload = {"tool_name": tool, "tool_input": tool_input, "cwd": "/repo"}
                self.assertEqual(claude_decision(run_guard("claude", payload)), expected)

    def test_antigravity_file_tools(self) -> None:
        cases = [
            ("view_file", {"AbsolutePath": "/repo/.env"}, DENY),
            ("view_file", {"AbsolutePath": "/repo/.env.template"}, ALLOW),
            ("view_file", {"AbsolutePath": "/home/u/.gemini/oauth_creds.json"}, DENY),
            ("read_url_content", {"Url": "https://example.com"}, ALLOW),
            ("view_file", {"AbsolutePath": "/repo/app.py"}, ALLOW),
        ]
        for tool, arguments, expected in cases:
            with self.subTest(tool=tool, arguments=arguments):
                payload = {"toolCall": {"name": tool, "args": arguments}, "workspacePaths": ["/repo"]}
                self.assertEqual(antigravity_decision(run_guard("antigravity", payload)), expected)

    def test_unparsable_input_fails_closed_only_for_clear_matches(self) -> None:
        clear = run_guard("claude", '{"tool_name": "Bash", "tool_input": {"command": "git push origin main"')
        self.assertEqual(claude_decision(clear), DENY)
        harmless = run_guard("claude", '{"tool_name": "Bash", "tool_input": {"command": "ls"')
        self.assertIsNone(claude_decision(harmless))
        self.assertIn("not blocking", harmless.stderr)

    def test_unclosed_quote_falls_back(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env 'unterminated"}}
        self.assertEqual(claude_decision(run_guard("claude", payload)), DENY)
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo 'unterminated"}}
        self.assertIsNone(claude_decision(run_guard("claude", payload)))


if __name__ == "__main__":
    unittest.main()
