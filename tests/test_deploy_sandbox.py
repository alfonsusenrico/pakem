"""End-to-end deploy test in a throwaway local Docker sandbox (opt-in: AF_SANDBOX=1).

The sandbox is one privileged docker:dind container that also runs sshd with a
non-root `deploy` user (the deployment host) and two forwarders standing in for the
public ingress. Its SSH and public ports are published on 127.0.0.1 only. The test
generates a throwaway SSH key, pins the sandbox host key in a temporary known_hosts,
deploys a tiny sample app through deploy_remote.sh, and checks promote, health,
automatic restore, manual rollback, retention, the lock, and the root owner mode.

Every container, volume, and image it creates carries the label
io.agent-framework.sandbox=<run id>; teardown removes them even when a step fails and
then fails the run if anything labelled remains. The sample app's base image is pulled
inside the sandbox and disappears with it; the docker:dind base image stays cached.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

# The documented entry point is `python3 -m unittest tests.test_deploy_sandbox` from the
# repository root, where tests/ is not on sys.path as it is under `discover -s tests`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import DEPLOY_ASSETS, REPO_ROOT, git, init_repo  # noqa: E402

ENABLED = os.environ.get("AF_SANDBOX") == "1"
LABEL_KEY = "io.agent-framework.sandbox"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "deploy_sandbox"
SCRIPT = DEPLOY_ASSETS / "deploy_remote.sh"
HOST_TOOL = "/usr/local/libexec/af-deploy/release_host.py"
STAGING_ROOT = "/home/deploy/apps/sample"
PRODUCTION_ROOT = "/srv/sample"
APP_SECRET = "dummy$HOME value"
REVISION_LABEL = "org.opencontainers.image.revision"


def docker(
    *args: str, check: bool = True, timeout: float = 600, input_text: str | None = None
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, input=input_text, check=False
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"docker {' '.join(args[:3])} failed ({completed.returncode}): {completed.stderr.strip()[-2000:]}"
        )
    return completed


@unittest.skipUnless(ENABLED, "set AF_SANDBOX=1 to run the local Docker deploy sandbox")
class DeploySandboxTests(unittest.TestCase):
    run_id: str
    label: str
    work: Path

    @classmethod
    def setUpClass(cls) -> None:
        for tool in ("docker", "ssh", "ssh-keygen", "git"):
            if shutil.which(tool) is None:
                raise unittest.SkipTest(f"{tool} is not installed")
        cls.run_id = f"af-sandbox-{secrets.token_hex(4)}"
        cls.label = f"{LABEL_KEY}={cls.run_id}"
        cls._temporary = tempfile.TemporaryDirectory(prefix="af-sandbox-")
        cls.work = Path(os.path.realpath(cls._temporary.name))
        cls.addClassCleanup(cls.teardown_sandbox)
        cls.create_key_and_host()
        cls.create_sample_repository()

    # -- sandbox lifecycle -------------------------------------------------------

    @classmethod
    def create_key_and_host(cls) -> None:
        keys = cls.work / "keys"
        keys.mkdir(mode=0o700)
        cls.key = keys / "id_sandbox"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", cls.run_id, "-f", str(cls.key)], check=True
        )
        context = cls.work / "host-context"
        shutil.copytree(FIXTURES / "host", context)
        shutil.copy(keys / "id_sandbox.pub", context / "authorized_keys")
        shutil.copy(DEPLOY_ASSETS / "release_host.py", context / "release_host.py")
        cls.image = f"{cls.run_id}-host:latest"
        docker("build", "--quiet", "--label", cls.label, "--tag", cls.image, str(context), timeout=900)
        volume = f"{cls.run_id}-docker"
        docker("volume", "create", "--label", cls.label, volume)
        docker(
            "run",
            "--detach",
            "--privileged",
            "--name",
            cls.run_id,
            "--label",
            cls.label,
            "--mount",
            f"type=volume,source={volume},target=/var/lib/docker",
            "--publish",
            "127.0.0.1::22",
            "--publish",
            "127.0.0.1::9080",
            "--publish",
            "127.0.0.1::9081",
            cls.image,
        )
        deadline = time.monotonic() + 120
        while docker("exec", cls.run_id, "docker", "info", check=False).returncode != 0:
            if time.monotonic() > deadline:
                raise AssertionError("dockerd in the sandbox did not start")
            time.sleep(1)
        cls.ssh_port = cls.published_port(22)
        cls.staging_url = f"http://127.0.0.1:{cls.published_port(9080)}"
        cls.production_url = f"http://127.0.0.1:{cls.published_port(9081)}"
        host_key = docker("exec", cls.run_id, "cat", "/etc/ssh/ssh_host_ed25519_key.pub").stdout.split()
        cls.known_hosts = keys / "known_hosts"
        cls.known_hosts.write_text(f"[127.0.0.1]:{cls.ssh_port} {host_key[0]} {host_key[1]}\n")
        cls.home = cls.work / "home"
        cls.home.mkdir()

    @classmethod
    def published_port(cls, container_port: int) -> int:
        address = docker("port", cls.run_id, f"{container_port}/tcp").stdout.strip().splitlines()[0]
        host, _, port = address.rpartition(":")
        if host != "127.0.0.1":
            raise AssertionError(f"sandbox port {container_port} is published on {host}, not 127.0.0.1")
        return int(port)

    @classmethod
    def teardown_sandbox(cls) -> None:
        for container in docker("ps", "--all", "--quiet", "--filter", f"label={cls.label}", check=False).stdout.split():
            docker("rm", "--force", "--volumes", container, check=False)
        for volume in docker("volume", "ls", "--quiet", "--filter", f"label={cls.label}", check=False).stdout.split():
            docker("volume", "rm", "--force", volume, check=False)
        for image in docker("image", "ls", "--quiet", "--filter", f"label={cls.label}", check=False).stdout.split():
            docker("image", "rm", "--force", image, check=False)
        for network in docker("network", "ls", "--quiet", "--filter", f"label={cls.label}", check=False).stdout.split():
            docker("network", "rm", network, check=False)
        cls._temporary.cleanup()
        leftovers = {
            kind: docker(*command, "--quiet", "--filter", f"label={cls.label}", check=False).stdout.split()
            for kind, command in {
                "containers": ("ps", "--all"),
                "volumes": ("volume", "ls"),
                "images": ("image", "ls"),
                "networks": ("network", "ls"),
            }.items()
        }
        if any(leftovers.values()):
            raise AssertionError(f"sandbox resources left behind: {leftovers}")

    # -- sample repository -------------------------------------------------------

    @classmethod
    def create_sample_repository(cls) -> None:
        cls.repo = cls.work / "repo"
        shutil.copytree(FIXTURES / "app", cls.repo)
        for hook in (cls.repo / "deploy" / "hooks").iterdir():
            os.chmod(hook, 0o755)
        init_repo(cls.repo, cls.home)
        cls.commits = {}
        cls.commit("one", "first release")
        cls.commit("two", "second release")
        compose = cls.repo / "compose.yaml"
        good_compose = compose.read_text()
        compose.write_text(good_compose.replace(':8000"', ':9999"'))
        cls.commit("three", "broken port mapping: candidate passes, live health fails")
        compose.write_text(good_compose)
        cls.commit("four", "fourth release")
        git(cls.repo, "tag", "v1.0.0", cls.commits["four"], home=cls.home)

    @classmethod
    def commit(cls, message: str, subject: str) -> None:
        (cls.repo / "message.txt").write_text(message + "\n")
        git(cls.repo, "add", ".", home=cls.home)
        git(cls.repo, "commit", "-q", "-m", subject, home=cls.home)
        cls.commits[message] = git(cls.repo, "rev-parse", "HEAD", home=cls.home).stdout.strip()

    # -- helpers -------------------------------------------------------------------

    def deploy(
        self, commit: str, environment: str = "staging", mode: str = "deploy", **overrides: str
    ) -> subprocess.CompletedProcess:
        production = environment == "production"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "TMPDIR": str(self.work),
            "GIT_CONFIG_NOSYSTEM": "1",
            "AF_DEPLOY_ENVIRONMENT": environment,
            "AF_DEPLOY_SHA": self.commits[commit],
            "AF_DEPLOY_MODE": mode,
            "DEPLOY_HOST": "127.0.0.1",
            "DEPLOY_USER": "deploy",
            "DEPLOY_SSH_PORT": str(self.ssh_port),
            "DEPLOY_ROOT": PRODUCTION_ROOT if production else STAGING_ROOT,
            "DEPLOY_OWNER": "root" if production else "self",
            "DEPLOY_SSH_KEY_FILE": str(self.key),
            "DEPLOY_KNOWN_HOSTS_FILE": str(self.known_hosts),
            "DEPLOY_PUBLIC_URL": self.production_url if production else self.staging_url,
            "AF_REPO_DIR": str(self.repo),
            "AF_PYTHON": sys.executable,
            "AF_RELEASE_REF": "main",
            "AF_LOCK_WAIT_SECONDS": "30",
            "AF_SMOKE_PATHS": "/ok.txt",
            "AF_SMOKE_ATTEMPTS": "15",
            "AF_DEPLOY_ACTOR": "sandbox-test",
            "AF_PIPELINE_URL": "https://ci.example.invalid/pipelines/1",
            "APP_SECRET": APP_SECRET,
        }
        if production:
            env["DEPLOY_HOST_TOOL"] = HOST_TOOL
        env.update(overrides)
        completed = subprocess.run(
            ["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=900, check=False
        )
        output = completed.stdout + completed.stderr
        log = self.work / "deploy.log"
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {mode} {commit} to {environment}: exit {completed.returncode}\n{output}")
        for secret in (APP_SECRET, *self.host_secrets()):
            self.assertNotIn(secret, output, "a runtime secret appeared in the deploy output")
        return completed

    def host(self, command: str, check: bool = True) -> str:
        return docker("exec", self.run_id, "sh", "-c", command, check=check).stdout

    def host_secrets(self) -> list[str]:
        values = []
        for root, environment in ((STAGING_ROOT, "staging"), (PRODUCTION_ROOT, "production")):
            text = self.host(f"cat {root}/{environment}/shared/host-generated.env 2>/dev/null || true")
            values += [
                line.split("=", 1)[1].strip("'")
                for line in text.splitlines()
                if "=" in line and not line.startswith("#")
            ]
        return values

    def public_revision(self, base_url: str) -> dict:
        with urllib.request.urlopen(f"{base_url}/release.json", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def current(self, root: str = STAGING_ROOT, environment: str = "staging") -> str:
        return self.host(f"readlink {root}/{environment}/current").strip().rsplit("/", 1)[-1]

    def record(self, root: str = STAGING_ROOT, environment: str = "staging") -> dict:
        return json.loads(self.host(f"cat {root}/{environment}/deployment.json"))

    def assert_exit(self, completed: subprocess.CompletedProcess, code: int) -> None:
        if completed.returncode != code:
            self.fail(f"expected exit {code}, got {completed.returncode}\n{completed.stdout}\n{completed.stderr}")

    # -- the scenario, in order ---------------------------------------------------------

    def test_01_first_deploy_promotes_and_reports_the_revision(self) -> None:
        sha = self.commits["one"]
        self.assert_exit(self.deploy("one"), 0)
        self.assertEqual(self.public_revision(self.staging_url), {"revision": sha, "message": "one"})
        self.assertEqual(self.current(), sha)
        record = self.record()
        self.assertEqual(
            (record["sha"], record["result"], record["mode"], record["actor"]),
            (sha, "succeeded", "deploy", "sandbox-test"),
        )
        hooks = self.host(f"cat {STAGING_ROOT}/staging/hooks.log").split()
        self.assertEqual(hooks, ["pre_build", sha, "pre_migrate", sha, "post_promote", sha])
        base = f"{STAGING_ROOT}/staging"
        stats = self.host(
            f"stat -c '%U %a %n' {base}/releases/{sha}/runtime.env {base}/shared/host-generated.env"
            f" {base}/releases/{sha}/source"
        )
        self.assertEqual(
            [line.split()[:2] for line in stats.splitlines()],
            [["deploy", "600"], ["deploy", "600"], ["deploy", "500"]],
        )
        label_format = f"'{{{{index .Config.Labels \"{REVISION_LABEL}\"}}}}'"
        label = self.host(f"docker image inspect --format {label_format} sample-staging/web:{sha}")
        self.assertEqual(label.strip(), sha)
        ports = self.host("docker ps --filter label=com.docker.compose.project=sample-staging --format '{{.Ports}}'")
        self.assertEqual(ports.strip(), "127.0.0.1:18080->8000/tcp")

    def test_02_second_deploy_replaces_the_running_release(self) -> None:
        sha = self.commits["two"]
        self.assert_exit(self.deploy("two"), 0)
        self.assertEqual(self.public_revision(self.staging_url)["revision"], sha)
        self.assertEqual(self.current(), sha)
        releases = set(self.host(f"ls {STAGING_ROOT}/staging/releases").split())
        self.assertEqual(releases, {self.commits["one"], sha})

    def test_03_failed_live_health_restores_the_previous_release(self) -> None:
        completed = self.deploy("three")
        self.assert_exit(completed, 3)
        self.assertIn("previous release was restored", completed.stderr)
        self.assertIn("candidate: http://127.0.0.1:", completed.stdout)
        previous = self.commits["two"]
        self.assertEqual(self.public_revision(self.staging_url)["revision"], previous)
        self.assertEqual(self.current(), previous)
        self.assertEqual(self.record()["result"], "restored")
        running = self.host(
            "docker ps --filter label=com.docker.compose.project=sample-staging"
            f" --format '{{{{.Label \"{REVISION_LABEL}\"}}}}'"
        )
        self.assertEqual(running.split(), [previous])

    def test_04_manual_rollback_redeploys_an_earlier_commit(self) -> None:
        sha = self.commits["one"]
        completed = self.deploy("one", mode="rollback")
        self.assert_exit(completed, 0)
        self.assertIn("skipping backup and migrations", completed.stdout)
        self.assertEqual(self.public_revision(self.staging_url)["revision"], sha)
        self.assertEqual(self.current(), sha)
        record = self.record()
        self.assertEqual(
            (record["mode"], record["result"], record["previous_sha"]), ("rollback", "succeeded", self.commits["two"])
        )
        hooks = self.host(f"cat {STAGING_ROOT}/staging/hooks.log").split()
        self.assertEqual(hooks.count("pre_migrate"), 3, "rollback must not run pre_migrate again")

    def test_05_cleanup_keeps_retention_and_removes_old_images(self) -> None:
        sha = self.commits["four"]
        self.assert_exit(self.deploy("four"), 0)
        self.assertEqual(self.public_revision(self.staging_url)["revision"], sha)
        kept = {self.commits["one"], sha}
        self.assertEqual(set(self.host(f"ls {STAGING_ROOT}/staging/releases").split()), kept)
        tags = self.host("docker image ls --format '{{.Tag}}' sample-staging/web").split()
        self.assertEqual(set(tags), kept)

    def test_06_lock_prevents_a_concurrent_deploy(self) -> None:
        holder = subprocess.Popen(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={self.known_hosts}",
                "-o",
                "LogLevel=ERROR",
                "-i",
                str(self.key),
                "-p",
                str(self.ssh_port),
                "deploy@127.0.0.1",
                f"/usr/bin/python3 {HOST_TOOL} lock --base {STAGING_ROOT}/staging --owner self"
                " --wait 0 --hold --token sandbox-holder-0123456789",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)},
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "AF-LOCK-ACQUIRED")
            completed = self.deploy("four", AF_LOCK_WAIT_SECONDS="2")
            self.assert_exit(completed, 75)
            self.assertIn("another deployment holds the staging lock", completed.stderr)
        finally:
            holder.stdin.close()
            holder.wait(timeout=60)
            holder.stdout.close()
        self.assertEqual(holder.returncode, 0)
        self.assertEqual(self.current(), self.commits["four"])
        status = json.loads(
            self.host(
                f"su deploy -s /bin/sh -c '/usr/bin/python3 {HOST_TOOL} status"
                f" --base {STAGING_ROOT}/staging --owner self --json'"
            )
        )
        self.assertFalse(status["lock_held"])

    def test_07_production_uses_root_owner_mode_backup_and_tags(self) -> None:
        self.assert_exit(self.deploy("two", environment="production"), 2)
        sha = self.commits["four"]
        completed = self.deploy("four", environment="production")
        self.assert_exit(completed, 0)
        self.assertEqual(self.public_revision(self.production_url)["revision"], sha)
        self.assertEqual(self.current(PRODUCTION_ROOT, "production"), sha)
        base = f"{PRODUCTION_ROOT}/production"
        owners = self.host(f"stat -c '%U %a' {base} {base}/releases/{sha}/source {base}/releases/{sha}/runtime.env")
        self.assertEqual(
            [line.split() for line in owners.splitlines()], [["root", "750"], ["root", "500"], ["root", "600"]]
        )
        record = self.record(PRODUCTION_ROOT, "production")
        self.assertTrue(record["backup"].startswith(f"{base}/backups/"))
        self.assertIn("backup of sample-production", self.host(f"cat {record['backup']}/marker.txt"))
        self.assertEqual(self.public_revision(self.staging_url)["revision"], sha, "staging must be untouched")


if __name__ == "__main__":
    unittest.main()
