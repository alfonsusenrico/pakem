"""The self-hosted runner on a fresh host (opt-in: AF_SANDBOX_RUNNER=1; slow, downloads).

Boots a systemd Debian 13 container with Debian's docker.io as the host (a private cgroup
namespace, as on a real machine), runs af_runner.sh install twice (the second run must
change nothing that matters), and proves the isolation and the job path: `check` passes,
the runner user's rootless Docker reaches the internet and a service container, the
production Docker socket refuses it, the slice caps apply, the daily Docker clean-up runs,
tokens are only accepted on standard input, and a public repository is refused. Registering a
real repository needs a GitHub token and is not tested here. The container is removed
afterwards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from support import REPO_ROOT

ENABLED = os.environ.get("AF_SANDBOX_RUNNER") == "1"
HOST_IMAGE = "pakem-runner-host:test"
CONTAINER = "pakem-runner-sandbox"
SCRIPT = REPO_ROOT / "skills" / "release-delivery" / "assets" / "deploy" / "af_runner.sh"
HOST_DOCKERFILE = textwrap.dedent(
    """\
    FROM debian:13
    RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd systemd-sysv dbus \\
        docker.io >/dev/null
    STOPSIGNAL SIGRTMIN+3
    CMD ["/sbin/init"]
    """
)
# The host's protected paths with the permissions a bootstrapped host gives them.
PREPARE = textwrap.dedent(
    """\
    set -e
    systemctl start docker
    mkdir -p /srv/deploy /etc/af/secrets /etc/af/hosts
    chmod 0700 /srv/deploy /etc/af/secrets
    chmod 0750 /etc/af/hosts
    # Docker Desktop's kernel builds ip_tables in, but the container has no module list.
    mkdir -p "/lib/modules/$(uname -r)"
    grep -q ip_tables /proc/modules || echo kernel/net/ipv4/netfilter/ip_tables.ko > "/lib/modules/$(uname -r)/modules.builtin"
    """
)
AS_RUNNER = (
    'uid=$(id -u ci-runner); runuser -u ci-runner -- env HOME=/var/lib/ci-runner '
    'XDG_RUNTIME_DIR=/run/user/$uid DOCKER_HOST=unix:///run/user/$uid/docker.sock '
    'PATH=/var/lib/ci-runner/bin:/usr/sbin:/usr/bin:/sbin:/bin '
)


def docker(*arguments: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *arguments], input=stdin, capture_output=True, text=True, check=check)


def host(command: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    flags = ["-i"] if stdin is not None else []
    return docker("exec", *flags, CONTAINER, "sh", "-c", command, check=check, stdin=stdin)


@unittest.skipUnless(ENABLED, "set AF_SANDBOX_RUNNER=1 to run the runner host sandbox")
class RunnerSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as context:
            Path(context, "Dockerfile").write_text(HOST_DOCKERFILE)
            docker("build", "-q", "-t", HOST_IMAGE, context)
        docker("rm", "-f", CONTAINER, check=False)
        docker(
            "run", "-d", "--name", CONTAINER, "--privileged", "--cgroupns=private",
            "--tmpfs", "/run", "--tmpfs", "/run/lock", HOST_IMAGE,
        )
        for _ in range(30):
            state = host("systemctl is-system-running", check=False).stdout.strip()
            if state in ("running", "degraded"):
                break
            subprocess.run(["sleep", "1"], check=True)
        docker("cp", str(SCRIPT), f"{CONTAINER}:/root/af_runner.sh")
        host(PREPARE)
        cls.first_install = host("/root/af_runner.sh install 2>&1")
        cls.second_install = host("/root/af_runner.sh install 2>&1")

    @classmethod
    def tearDownClass(cls) -> None:
        docker("rm", "-f", CONTAINER, check=False)

    def test_install_is_idempotent(self) -> None:
        self.assertIn("creating user ci-runner", self.first_install.stdout)
        self.assertNotIn("creating user", self.second_install.stdout)
        self.assertIn("rootless Docker for ci-runner is already running", self.second_install.stdout)
        self.assertIn("reach the host's loopback", self.first_install.stdout)
        self.assertNotIn("reach the host's loopback", self.second_install.stdout)

    def test_check_passes(self) -> None:
        completed = host("/root/af_runner.sh check 2>&1", check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("all checks passed", completed.stdout)
        self.assertIn("ok    ci-runner cannot read or write /var/run/docker.sock", completed.stdout)
        self.assertIn("ok    daily Docker clean-up timer is active", completed.stdout)

    def test_prune_bounds_the_runners_docker(self) -> None:
        self.assertIn("daily Docker clean-up", self.first_install.stdout)
        self.assertNotIn("daily Docker clean-up", self.second_install.stdout)
        host(AS_RUNNER + "docker pull -q alpine:3")
        completed = host("/root/af_runner.sh prune 2>&1", check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("after:", completed.stdout)
        unit = host(AS_RUNNER + "systemctl --user cat pakem-docker-prune.service").stdout
        # Debian 13's docker.io 26.1 has BuildKit older than v0.17 and buildx 0.13, so only
        # the last flag works here: the prune above passing proves the fallback.
        self.assertIn("for limit in --max-used-space --reserved-space --keep-storage", unit)
        self.assertIn("builder prune --force $$limit 10GB", unit)
        self.assertIn("image prune --all --force --filter until=72h", unit)

    def test_jobs_reach_the_internet_and_service_containers(self) -> None:
        outbound = host(AS_RUNNER + 'docker run --rm alpine:3 sh -c "wget -q -O /dev/null https://github.com && echo ok"')
        self.assertIn("ok", outbound.stdout)
        host(AS_RUNNER + "docker network create jobnet")
        host(AS_RUNNER + "docker run -d --name svc-db --network jobnet -e POSTGRES_PASSWORD=test postgres:16-alpine")
        ready = ""
        for _ in range(30):
            ready = host(
                AS_RUNNER + "docker run --rm --network jobnet postgres:16-alpine pg_isready -h svc-db -U postgres",
                check=False,
            ).stdout
            if "accepting connections" in ready:
                break
        self.assertIn("accepting connections", ready)
        host(AS_RUNNER + "docker rm -f svc-db; docker network rm jobnet", check=False)

    def test_job_containers_reach_a_server_on_the_host_loopback(self) -> None:
        host(AS_RUNNER + "sh -c 'cd /tmp && echo loopback-ok > index.html && "
             "(timeout 60 python3 -m http.server 18765 --bind 127.0.0.1 >/dev/null 2>&1 &)'")
        completed = host(AS_RUNNER + "docker run --rm alpine:3 wget -q -O - http://10.0.2.2:18765/index.html",
                         check=False)
        self.assertIn("loopback-ok", completed.stdout, completed.stderr)

    def test_production_docker_refuses_the_runner(self) -> None:
        completed = host("runuser -u ci-runner -- docker -H unix:///var/run/docker.sock ps", check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("permission denied", completed.stderr + completed.stdout)

    def test_slice_caps_apply(self) -> None:
        slice_dir = "/sys/fs/cgroup/user.slice/user-$(id -u ci-runner).slice"
        caps = host(f'cat {slice_dir}/memory.max {slice_dir}/cpu.max {slice_dir}/pids.max').stdout.split()
        # Half the CPUs and 40% of the memory, at least 2 CPUs and 4 GiB.
        cpus = int(host("nproc").stdout)
        mem_kib = int(host("awk '/^MemTotal:/ { print $2 }' /proc/meminfo").stdout)
        quota = cpus * 50 if cpus > 4 else 200
        gib = max(mem_kib * 4 // 10 // 1048576, 4)
        self.assertEqual(caps, [str(gib * 1024**3), str(quota * 1000), "100000", "4096"])

    def test_a_public_repository_is_refused(self) -> None:
        completed = host("echo AAAAAAAAAAAAAAAAAAAAAAAA | /root/af_runner.sh add-repo actions/runner 2>&1", check=False)
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("actions/runner is public", completed.stdout)
        unreachable = host("echo AAAAAAAAAAAAAAAAAAAAAAAA | GITHUB_API=http://127.0.0.1:9 "
                           "/root/af_runner.sh add-repo owner/repo 2>&1", check=False)
        self.assertIn("could not confirm with GitHub that owner/repo is private", unreachable.stdout)

    def test_adding_runners_keeps_a_registered_one(self) -> None:
        # A registered instance, as config.sh leaves it: config.sh refuses to configure it again.
        directory = "/var/lib/ci-runner/runners/owner-repo"
        host(f"""set -e
            mkdir -p {directory}
            printf '{{"gitHubUrl": "https://github.com/owner/repo"}}' > {directory}/.runner
            printf '#!/bin/sh\\nexec sleep infinity\\n' > {directory}/run.sh
            chmod 0755 {directory}/run.sh
            chown -R ci-runner:ci-runner {directory}
            mkdir -p /tmp/api && cd /tmp/api && (python3 -m http.server 8765 >/dev/null 2>&1 &)
            sleep 1""")
        try:
            # The local server answers 404, as GitHub does for a private repository.
            completed = host("echo AAAAAAAAAAAAAAAAAAAAAAAA | GITHUB_API=http://127.0.0.1:8765 "
                             "/root/af_runner.sh add-repo owner/repo 2>&1", check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("already serves owner/repo", completed.stdout)
            self.assertNotIn("registering", completed.stdout)
        finally:
            host(AS_RUNNER + "systemctl --user disable --now actions-runner@owner-repo", check=False)
            host(f"rm -rf {directory}; pkill -f 'http.server 8765'", check=False)

    def test_tokens_only_on_standard_input(self) -> None:
        cases = [
            ("/root/af_runner.sh add-repo owner/repo </dev/null", "does not hold a runner token"),
            ("echo 'not a token!' | /root/af_runner.sh add-repo owner/repo", "does not hold a runner token"),
            ("echo AAAAAAAAAAAAAAAAAAAAAAAA | /root/af_runner.sh add-repo 'bad name'", "must look like owner/repo"),
            ("echo AAAAAAAAAAAAAAAAAAAAAAAA | /root/af_runner.sh add-repo owner/repo 9", "count must be a number from 1 to 8"),
            ("echo AAAAAAAAAAAAAAAAAAAAAAAA | /root/af_runner.sh add-repo owner/repo two", "count must be a number from 1 to 8"),
            ("script -qec '/root/af_runner.sh add-repo owner/repo' /dev/null </dev/null", "Never pass it as an argument"),
            ("echo AAAAAAAAAAAAAAAAAAAAAAAA | /root/af_runner.sh remove-repo owner/missing", "no runner for owner/missing"),
        ]
        for command, needle in cases:
            with self.subTest(command=command):
                completed = host(command + " 2>&1", check=False)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(needle, completed.stdout)


if __name__ == "__main__":
    unittest.main()
