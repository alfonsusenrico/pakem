"""Input validation of the deploy asset deploy_remote.sh: invalid inputs stop before SSH.

A fake `ssh` earlier on PATH records every call and fails, so each test shows whether
the driver tried to connect. Valid inputs reach the fake ssh (positive control).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from support import DEPLOY_ASSETS, git, init_repo, write

SCRIPT = DEPLOY_ASSETS / "deploy_remote.sh"
# AF_TEST_SHELL=dash runs the driver under the shell Debian and Ubuntu use as sh.
SHELL = os.environ.get("AF_TEST_SHELL", "sh")

RELEASE_TOML = textwrap.dedent(
    """\
    app = "demo"
    compose_file = "compose.yaml"
    service = "web"
    candidate_port = 8000
    health_path = "/release.json"
    preflight = []
    migrate = []

    [[images]]
    name = "web"

    [environments.staging]
    local_url = "http://127.0.0.1:18080"

    [environments.production]
    local_url = "http://127.0.0.1:18081"
    """
)
MANIFEST = textwrap.dedent(
    """\
    required = ["APP_SECRET"]

    [defaults]
    staging = "deploy/runtime/staging.env"
    production = "deploy/runtime/production.env"
    """
)


@unittest.skipUnless(shutil.which("ssh-keygen") and shutil.which("git"), "needs ssh-keygen and git")
class DeployRemoteValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="af-deploy-remote-")
        self.base = Path(os.path.realpath(self._temporary.name))
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = init_repo(self.base / "repo", self.home)
        write(self.repo / "deploy" / "release.toml", RELEASE_TOML)
        write(self.repo / "deploy" / "runtime.manifest.toml", MANIFEST)
        write(self.repo / "deploy" / "runtime" / "staging.env", "HTTP_PORT=18080\n")
        write(self.repo / "deploy" / "runtime" / "production.env", "HTTP_PORT=18081\n")
        git(self.repo, "add", ".", home=self.home)
        git(self.repo, "commit", "-q", "-m", "first", home=self.home)
        self.sha = git(self.repo, "rev-parse", "HEAD", home=self.home).stdout.strip()

        keys = self.base / "keys"
        keys.mkdir()
        self.key = keys / "id_deploy"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "af-test", "-f", str(self.key)], check=True
        )
        public = (keys / "id_deploy.pub").read_text().split()
        self.known_hosts = keys / "known_hosts"
        self.known_hosts.write_text(f"[deploy.example.invalid]:2222 {public[0]} {public[1]}\n")

        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        self.ssh_log = self.base / "ssh-calls.log"
        write(self.fakebin / "ssh", f'#!/bin/sh\necho "$@" >> "{self.ssh_log}"\nexit 99\n', mode=0o755)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def env(self, **overrides: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.fakebin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.home),
            "TMPDIR": str(self.base),
            "GIT_CONFIG_NOSYSTEM": "1",
            "AF_DEPLOY_ENVIRONMENT": "staging",
            "AF_DEPLOY_SHA": self.sha,
            "DEPLOY_HOST": "deploy.example.invalid",
            "DEPLOY_USER": "deploy",
            "DEPLOY_SSH_PORT": "2222",
            "DEPLOY_ROOT": "/srv/demo",
            "DEPLOY_OWNER": "self",
            "DEPLOY_SSH_KEY_FILE": str(self.key),
            "DEPLOY_KNOWN_HOSTS_FILE": str(self.known_hosts),
            "DEPLOY_PUBLIC_URL": "https://demo.example.invalid",
            "AF_REPO_DIR": str(self.repo),
            "AF_RELEASE_REF": "main",
            "APP_SECRET": "validation-secret-value",
        }
        env.update(overrides)
        return {key: value for key, value in env.items() if value is not None}

    def run_driver(self, *args: str, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [SHELL, str(SCRIPT), *args], env=self.env(**overrides), capture_output=True, text=True, check=False
        )

    def assert_invalid_before_ssh(self, expected: str, *args: str, **overrides: str) -> None:
        completed = self.run_driver(*args, **overrides)
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2, output)
        self.assertIn(expected, output)
        self.assertFalse(self.ssh_log.exists(), "the driver connected despite invalid input")
        self.assertNotIn("validation-secret-value", output)

    def test_valid_inputs_reach_ssh_after_materializing_the_runtime(self) -> None:
        completed = self.run_driver()
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 1, output)
        self.assertIn("could not connect", output)
        calls = self.ssh_log.read_text()
        for option in (
            "-F /dev/null",
            "StrictHostKeyChecking=yes",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "BatchMode=yes",
        ):
            self.assertIn(option, calls)
        self.assertNotIn("accept-new", calls)
        self.assertNotIn("validation-secret-value", output)
        self.assertEqual([path for path in self.base.iterdir() if path.name.startswith("af-deploy.")], [])

    def test_malformed_identifiers_are_rejected(self) -> None:
        cases = [
            ("AF_DEPLOY_SHA", "abc123", "AF_DEPLOY_SHA"),
            ("AF_DEPLOY_SHA", self.sha.upper(), "AF_DEPLOY_SHA"),
            ("AF_DEPLOY_ENVIRONMENT", "Staging", "AF_DEPLOY_ENVIRONMENT"),
            ("AF_DEPLOY_ENVIRONMENT", "staging;id", "AF_DEPLOY_ENVIRONMENT"),
            ("AF_DEPLOY_MODE", "destroy", "AF_DEPLOY_MODE"),
            ("DEPLOY_HOST", "host;reboot", "DEPLOY_HOST"),
            ("DEPLOY_HOST", "-oProxyCommand=x", "DEPLOY_HOST"),
            ("DEPLOY_USER", "root;id", "DEPLOY_USER"),
            ("DEPLOY_SSH_PORT", "22x", "DEPLOY_SSH_PORT"),
            ("DEPLOY_ROOT", "srv/demo", "DEPLOY_ROOT"),
            ("DEPLOY_ROOT", "/srv/../etc", "DEPLOY_ROOT"),
            ("DEPLOY_ROOT", "/srv/demo $(id)", "DEPLOY_ROOT"),
            ("DEPLOY_OWNER", "admin", "DEPLOY_OWNER"),
            ("DEPLOY_PUBLIC_URL", "http://demo.example.invalid", "DEPLOY_PUBLIC_URL"),
            ("AF_SMOKE_PATHS", "/ok relative", "AF_SMOKE_PATHS"),
            ("AF_PIPELINE_URL", "https://ci.example.invalid/$(id)", "AF_PIPELINE_URL"),
        ]
        for name, value, expected in cases:
            with self.subTest(name=name, value=value):
                self.assert_invalid_before_ssh(expected, **{name: value})

    def test_credentials_files_are_checked(self) -> None:
        self.assert_invalid_before_ssh("DEPLOY_SSH_KEY_FILE", DEPLOY_SSH_KEY_FILE=str(self.base / "missing"))
        self.assert_invalid_before_ssh("DEPLOY_KNOWN_HOSTS_FILE", DEPLOY_KNOWN_HOSTS_FILE=str(self.base / "missing"))
        os.chmod(self.key, 0o644)
        self.assert_invalid_before_ssh("readable by group or others")
        os.chmod(self.key, 0o600)
        self.assert_invalid_before_ssh(
            "no pinned key for [other.example.invalid]:2222", DEPLOY_HOST="other.example.invalid"
        )

    def test_root_owner_with_a_non_root_user_needs_the_installed_tool(self) -> None:
        self.assert_invalid_before_ssh("needs DEPLOY_HOST_TOOL", DEPLOY_OWNER="root")

    def test_production_needs_a_tagged_commit_on_the_release_branch(self) -> None:
        self.assert_invalid_before_ssh("deploys only commits tagged v*", AF_DEPLOY_ENVIRONMENT="production")
        git(self.repo, "checkout", "-q", "-b", "side", home=self.home)
        write(self.repo / "side.txt", "side\n")
        git(self.repo, "add", ".", home=self.home)
        git(self.repo, "commit", "-q", "-m", "side", home=self.home)
        side = git(self.repo, "rev-parse", "HEAD", home=self.home).stdout.strip()
        git(self.repo, "tag", "v9.9.9", side, home=self.home)
        self.assert_invalid_before_ssh("is not an ancestor", AF_DEPLOY_ENVIRONMENT="production", AF_DEPLOY_SHA=side)

    def test_unknown_commits_and_arguments_are_rejected(self) -> None:
        self.assert_invalid_before_ssh("is not in", AF_DEPLOY_SHA="0" * 40)
        self.assert_invalid_before_ssh("takes no arguments", "--force")

    def test_invalid_runtime_configuration_stops_before_ssh(self) -> None:
        self.assert_invalid_before_ssh("runtime configuration is invalid", APP_SECRET="")
        self.assert_invalid_before_ssh("runtime configuration is invalid", APP_SECRET="two\nlines")


if __name__ == "__main__":
    unittest.main()
