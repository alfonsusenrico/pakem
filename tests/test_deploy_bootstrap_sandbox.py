"""A fresh host to a working staging deployment (opt-in: AF_SANDBOX_HOST=1; slow, downloads).

Boots a systemd Debian 13 container as the host, runs af_bootstrap.sh install (twice: the
second run must change nothing), then acts as CI inside it (a loopback registry, cosign key,
and af_publish.py --local) and as the operator (contracts §11 steps 3 to 7: key, host
config, placement, secret). The pull agent must deploy the release, Caddy must route the
staging domain to it, and the monitor must pass. The container is removed afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from engine_sandbox import APP, COMPOSE, RELEASE_TOML, SETTINGS_TOML
from support import REPO_ROOT

ENABLED = os.environ.get("AF_SANDBOX_HOST") == "1"
COMMIT = "9" * 40
HOST_IMAGE = "af-bootstrap-host:test"
HOST_DOCKERFILE = textwrap.dedent(
    """\
    FROM debian:13
    RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd systemd-sysv dbus \\
        >/dev/null && rm -rf /var/lib/apt/lists/*
    STOPSIGNAL SIGRTMIN+3
    CMD ["/sbin/init"]
    """
)
PROJECT_DOCKERFILE = textwrap.dedent(
    """\
    FROM python:3.13-alpine
    ARG AF_REVISION
    RUN printf '%s' "$AF_REVISION" > /revision
    COPY app /app
    WORKDIR /
    USER nobody
    CMD ["python", "-m", "app.server"]
    """
)
ROUTED_RELEASE = RELEASE_TOML + textwrap.dedent(
    """\

    [[routes]]
    host_setting = "PUBLIC_HOST"
    service = "web"
    port = 8000
    """
)
ORAS = "https://github.com/oras-project/oras/releases/download/v1.3.5/oras_1.3.5_linux_${ARCH}.tar.gz"
# From oras_1.3.5_checksums.txt; the host container runs the machine's architecture.
ORAS_SHA256_AMD64 = "f48a73ea6d026a862c3919fc773da9a8c86fc259c2c737d5baed3b8b9614ad22"
ORAS_SHA256_ARM64 = "d5d4e5afd5c0781c93eda5ea1976e654e1fdee202c1ddaeaf5f91a8c4fd67d46"
GATE = textwrap.dedent(
    f"""\
    set -euo pipefail
    DEPLOY=/framework/skills/release-delivery/assets/deploy
    # -- CI: a loopback registry, the project's signing key, and one published release --
    docker run -d --name registry -p 127.0.0.1:5000:5000 registry:3 >/dev/null
    ARCH=$(dpkg --print-architecture)
    if [ "$ARCH" = amd64 ]; then ORAS_SHA256={ORAS_SHA256_AMD64}; else ORAS_SHA256={ORAS_SHA256_ARM64}; fi
    curl -fsSL -o /tmp/oras.tar.gz "{ORAS}"
    echo "$ORAS_SHA256  /tmp/oras.tar.gz" | sha256sum --check --strict --status
    tar -xzf /tmp/oras.tar.gz -C /usr/local/bin oras
    install -d -m 0700 /root/keys && cd /root/keys
    COSIGN_PASSWORD= cosign generate-key-pair >/dev/null 2>&1
    cp -r /work/project /root/project && cd /root/project
    COSIGN_PRIVATE_KEY="$(cat /root/keys/cosign.key)" COSIGN_PASSWORD= \\
      python3 -B "$DEPLOY/af_publish.py" publish --local --repository 127.0.0.1:5000/owner/sandbox \\
      --commit {COMMIT} --source https://example.test/owner/sandbox --ci-run https://example.test/run/1 >/dev/null
    # -- operator: contracts §11 steps 3 to 7 --
    install -m 0640 -o root -g af /root/keys/cosign.pub /etc/af/keys/sandbox.pub
    cat > /etc/af/hosts/sandbox-staging.toml <<'EOF'
    project = "sandbox"
    environment = "staging"
    base = "/srv/deploy/sandbox/staging"
    pointer = "127.0.0.1:5000/owner/sandbox:release-staging"
    public_key = "/etc/af/keys/sandbox.pub"

    [proxy]
    sites = "/etc/caddy/sites"
    reload = ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--address", "unix//run/caddy/admin.sock"]
    tls = false
    EOF
    chmod 0640 /etc/af/hosts/sandbox-staging.toml && chgrp af /etc/af/hosts/sandbox-staging.toml
    install -d -m 0700 -o af -g af /srv/deploy/sandbox /srv/deploy/sandbox/staging /srv/deploy/sandbox/staging/secrets
    printf 'HTTP_PORT=18080\\nPUBLIC_HOST=staging.example.test\\n' > /srv/deploy/sandbox/staging/placement.env
    chown af:af /srv/deploy/sandbox/staging/placement.env && chmod 0600 /srv/deploy/sandbox/staging/placement.env
    printf 'sandbox-secret' | runuser -u af -- python3 /opt/af/af_engine.py secret-set \\
      --config /etc/af/hosts/sandbox-staging.toml DATABASE_PASSWORD
    # -- the pull agent deploys; Caddy routes; the monitor passes --
    systemctl start af-agent.service || {{ journalctl -u af-agent.service --no-pager -o cat | tail -40; exit 1; }}
    python3 -c 'import json; r = json.load(open("/srv/deploy/sandbox/staging/deployment.json")); print("result", r["result"], r["trigger"])'
    echo "routed $(curl -fsS -H 'Host: staging.example.test' http://127.0.0.1/health/release)"
    systemctl start af-monitor.service || {{ journalctl -u af-monitor.service --no-pager -o cat | tail -40; exit 1; }}
    echo "monitor ok"
    journalctl -u af-agent.service -u af-monitor.service --no-pager -o cat | grep -c 'sandbox-secret' || true
    """
)


def docker(*arguments: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=check, timeout=timeout)


@unittest.skipUnless(ENABLED, "set AF_SANDBOX_HOST=1 to bootstrap a container host")
class FreshHostToStaging(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("docker is not installed")
        work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (work / "host").mkdir()
        (work / "host" / "Dockerfile").write_text(HOST_DOCKERFILE)
        docker("build", "-q", "-t", HOST_IMAGE, str(work / "host"))
        project = work / "project"
        files = {
            **APP,
            "Dockerfile": PROJECT_DOCKERFILE,
            "compose.yaml": COMPOSE,
            "migrations/0001_initial.sql": "-- sandbox\n",
            "deploy/release.toml": ROUTED_RELEASE,
            "deploy/settings.toml": SETTINGS_TOML,
            "deploy/env/staging.env": "LOG_LEVEL=debug\n",
            "deploy/env/production.env": "LOG_LEVEL=info\n",
        }
        for name, content in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(content)
        (work / "gate.sh").write_text(GATE)
        self.name = f"af-bootstrap-host-{os.getpid()}"
        docker(
            # A private cgroup namespace keeps the host's Docker away from processes outside it.
            "run", "-d", "--name", self.name, "--privileged", "--cgroupns=private",
            "-v", f"{REPO_ROOT}:/framework:ro", "-v", f"{work}:/work:ro",
            # The host's Docker needs storage that is not itself overlayfs (anonymous volumes).
            "-v", "/var/lib/docker", "-v", "/var/lib/containerd",
            HOST_IMAGE,
        )
        self.addCleanup(docker, "rm", "--force", "--volumes", self.name, check=False)
        self.addCleanup(docker, "rmi", HOST_IMAGE, check=False)

    def run_in_host(self, script: str) -> str:
        result = docker("exec", self.name, "bash", "-c", script, check=False)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        return result.stdout

    def test_bootstrap_then_a_staging_release_is_deployed_and_routed(self) -> None:
        bootstrap = "bash /framework/skills/release-delivery/assets/deploy/af_bootstrap.sh install"
        first = self.run_in_host(bootstrap)
        self.assertIn("host ready", first)
        again = self.run_in_host(bootstrap)
        self.assertIn("docker present", again)
        self.assertIn("caddy present", again)
        self.assertIn("cosign v3.1.3 present", again)
        self.assertNotIn("FAIL", again)
        self.assertIn("reloading systemd", first)
        self.assertNotIn("reloading systemd", again)  # nothing changed, so no reload

        output = self.run_in_host("bash /work/gate.sh")
        self.assertIn("result deployed pull", output)
        self.assertIn(f'routed {{"revision": "{COMMIT}"}}', output)
        self.assertIn("monitor ok", output)
        self.assertTrue(output.strip().endswith("0"), "the secret value appeared in the journal")


if __name__ == "__main__":
    unittest.main()
