"""Direct delivery end to end over real SSH (opt-in: AF_SANDBOX_HOST=1; slow, downloads).

A systemd Debian 13 container is the host. Its port 22 lets the deploy user in without a
key, as Tailscale SSH does, so `pakem setup host --project-dir` must refuse; with
`--release-port`, one run makes the device key, installs the engine with a second OpenSSH
port, and lets the key release a small web project there. Then, with only the
device key: publish two commits to staging (each built on the host), promote the second
to production (reusing its images), roll staging back, read the history, and revoke the
device. A fake gh stands in for GitHub's check results; everything else is real.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import unittest

from engine_sandbox import APP, BACKUP_HOOK, COMPOSE, RELEASE_TOML, SETTINGS_TOML, free_port
from support import git, init_repo, write
from test_setup_host_sandbox import ENABLED, HostOverSsh

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
FAKE_GH = """#!/bin/sh
case "$1 $2" in
  "auth status") echo "Logged in to github.com" ;;
  "api --paginate") printf 'CI\\tcompleted\\tsuccess\\n' ;;
  "api repos/"*) ;;
  *) echo "gh version 2.0" ;;
esac
"""
PORTS = {"staging": 18080, "production": 18081}


@unittest.skipUnless(ENABLED, "set AF_SANDBOX_HOST=1 to release over SSH to a container host")
class DirectReleaseOverSsh(HostOverSsh):
    def published_ports(self) -> list[str]:
        self.release_port = free_port()
        return ["-p", f"127.0.0.1:{self.release_port}:{self.release_port}"]

    def setUp(self) -> None:
        super().setUp()
        write(self.bin / "gh", FAKE_GH, mode=0o755)
        self.project = init_repo(self.home.parent / "sandbox", self.home)
        files = {
            **APP,
            "Dockerfile": PROJECT_DOCKERFILE,
            "compose.yaml": COMPOSE,
            "migrations/0001_initial.sql": "-- sandbox\n",
            "deploy/release.toml": RELEASE_TOML,
            "deploy/settings.toml": SETTINGS_TOML,
            "deploy/env/staging.env": "LOG_LEVEL=debug\n",
            "deploy/env/production.env": "LOG_LEVEL=info\n",
        }
        for name, content in files.items():
            write(self.project / name, content)
        write(self.project / "deploy" / "hooks" / "backup", BACKUP_HOOK, mode=0o755)
        origin = self.home.parent / "origin.git"
        git(self.home.parent, "init", "-q", "--bare", str(origin), home=self.home)
        url = "https://github.com/owner/sandbox.git"
        git(self.project, "remote", "add", "origin", url, home=self.home)
        git(self.project, "config", f"url.{origin}.insteadOf", url, home=self.home)
        self.addCleanup(self.in_host_quietly, "docker ps -aq | xargs -r docker rm -f")

    def in_host_quietly(self, script: str) -> None:
        subprocess.run(["docker", "exec", self.name, "bash", "-c", script], capture_output=True, check=False)

    def device_ssh(self, command: str) -> subprocess.CompletedProcess:
        key = self.home / ".ssh" / "pakem_device_ed25519"
        return subprocess.run(
            [str(self.bin / "ssh"), "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
             "-p", str(self.release_port), "af@pakem-sandbox", command],
            env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )

    def commit(self, message: str) -> str:
        write(self.project / "CHANGES.md", message + "\n")
        git(self.project, "add", "-A", home=self.home)
        git(self.project, "commit", "-q", "-m", message, home=self.home)
        git(self.project, "push", "-q", "origin", "main", home=self.home)
        return git(self.project, "rev-parse", "HEAD", home=self.home).stdout.strip()

    def release(self, *args: str, timeout: float = 1200) -> tuple[int, str]:
        return self.pakem("release", *args, "--project-dir", str(self.project), timeout=timeout)

    def served(self, environment: str) -> str:
        body = self.in_host(f"curl -fsS http://127.0.0.1:{PORTS[environment]}/health/release")
        return json.loads(body)["revision"]

    def test_publish_promote_rollback_and_revoke(self) -> None:
        first = self.commit("feat: first")
        waivers = ("--allow-unmerged", "--allow-dirty")
        code, output = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--yes", *waivers)
        self.assertEqual(code, 0, output[-4000:])
        # Port 22 now lets the deploy user in with no key, as Tailscale SSH does: forced commands
        # never run there, so setup must notice and name the release port.
        self.in_host(
            "passwd -q -d af && printf 'Match LocalPort 22 User af\\n  PermitEmptyPasswords yes\\n"
            "  PasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/00-keyless-af.conf && sshd -t "
            "&& systemctl reload ssh"
        )
        code, output = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--no-passphrase",
                                  "--project-dir", str(self.project), "--yes", *waivers)
        self.assertEqual(code, 5, output[-4000:])
        self.assertIn("gave the device key a shell instead of the release receiver", output)
        # A port another program holds (here a listener like a Git server's SSH) is refused.
        self.in_host("(nohup python3 -c 'import socket,time; s=socket.socket(); s.bind((\"0.0.0.0\", 39222)); "
                     "s.listen(); time.sleep(3600)' >/dev/null 2>&1 &) ; sleep 1")
        code, output = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--release-port", "39222",
                                  "--project-dir", str(self.project), "--yes", *waivers)
        self.assertNotEqual(code, 0, output[-4000:])
        self.assertIn("port 39222 is already used by python3", output)
        # The one owner step: the device key, the host, a release port, and this project.
        code, output = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--release-port",
                                  str(self.release_port), "--project-dir", str(self.project), "--yes", *waivers)
        self.assertEqual(code, 0, output[-4000:])
        self.assertIn(f"sshd: release port {self.release_port}", output)
        self.assertIn("missing placement settings: HTTP_PORT, PUBLIC_HOST", output)
        self.assertIn("This device may release sandbox there", output)
        for environment, port in PORTS.items():
            base = f"/srv/deploy/sandbox/{environment}"
            self.in_host(
                f"printf 'HTTP_PORT={port}\\nPUBLIC_HOST={environment}.example.test\\n' > {base}/placement.env"
                f" && chown af:af {base}/placement.env && chmod 0600 {base}/placement.env"
                f" && printf 'sandbox-secret' | runuser -u af -- python3 /opt/af/af_engine.py secret-set"
                f" --config /etc/af/hosts/sandbox-{environment}.toml DATABASE_PASSWORD"
            )

        code, output = self.release("publish", "--yes")
        self.assertEqual(code, 0, output[-4000:])
        self.assertIn("building web", output)
        self.assertEqual(self.served("staging"), first)
        self.assertNotIn("sandbox-secret", output)

        second = self.commit("feat: second")
        code, output = self.release("publish", "--yes")
        self.assertEqual(code, 0, output[-4000:])
        self.assertEqual(self.served("staging"), second)

        code, output = self.release("promote", "v1.0.0", "--production", "--yes")
        self.assertEqual(code, 0, output[-4000:])
        self.assertIn("reusing the images built for staging", output)
        self.assertNotIn("building web", output)
        self.assertEqual(self.served("production"), second)
        tagged = git(self.project, "ls-remote", "--tags", "origin", home=self.home).stdout
        self.assertIn(f"{second}\trefs/tags/v1.0.0^{{}}", tagged, "the annotated tag names the promoted commit")

        code, output = self.release("rollback", "previous", "--yes")
        self.assertEqual(code, 0, output[-4000:])
        self.assertEqual(self.served("staging"), first)

        code, output = self.release("history", timeout=120)
        self.assertEqual(code, 0, output[-4000:])
        self.assertIn(f"staging: running {first[:12]}", output)
        self.assertIn(f"production: running {second[:12]}", output)
        self.assertIn("v1.0.0", output)
        built = self.in_host("docker image ls --format '{{.Repository}}:{{.Tag}}' 'pakem/sandbox-web'")
        self.assertEqual(sorted(built.split()), sorted(f"pakem/sandbox-web:{commit}" for commit in (first, second)))

        shell = self.device_ssh("id")
        self.assertEqual(shell.returncode, 2, "the device key reaches only the release receiver")
        self.assertIn("expected `release`", shell.stdout)

        code, output = self.pakem("host", "revoke-device", "sandbox", "--yes", timeout=120)
        self.assertEqual(code, 0, output[-4000:])
        code, output = self.release("publish", "--plan", timeout=120)
        self.assertEqual(code, 3, output[-2000:])
        self.assertIn("run `pakem setup host sandbox` in your terminal", output)
        self.assertNotEqual(self.device_ssh("status sandbox").returncode, 0, "the host no longer takes the key")
        signers = self.in_host("cat /etc/af/allowed_signers; cat /var/lib/af/.ssh/authorized_keys 2>/dev/null || true")
        self.assertNotIn("pakem-device", signers)


if __name__ == "__main__":
    unittest.main()
