"""pakem setup host against a real SSH server (opt-in: AF_SANDBOX_HOST=1; slow, downloads).

Boots a systemd Debian 13 container with sshd and an admin user (passwordless sudo, a
throwaway key standing in for the owner's own login), then runs `pakem setup host`
twice under a pseudo-terminal over real SSH, and `pakem setup check --host`. The
second setup must change nothing. The container is removed afterwards.
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from support import PAKEM, REPO_ROOT, isolated_env

ENABLED = os.environ.get("AF_SANDBOX_HOST") == "1"
HOST_IMAGE = "pakem-setup-host:test"
HOST_DOCKERFILE = textwrap.dedent(
    """\
    FROM debian:13
    RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \\
        systemd systemd-sysv dbus openssh-server sudo >/dev/null && rm -rf /var/lib/apt/lists/* \\
        && useradd --create-home --shell /bin/bash admin \\
        && echo 'admin ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/admin \\
        && systemctl enable ssh
    STOPSIGNAL SIGRTMIN+3
    CMD ["/sbin/init"]
    """
)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and completed.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} failed: {completed.stderr}")
    return completed


def run_tty(argv: list[str], env: dict, timeout: float) -> tuple[int, str]:
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(argv[0], argv, env)
    output, deadline = b"", time.monotonic() + timeout
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


@unittest.skipUnless(ENABLED, "set AF_SANDBOX_HOST=1 to set up a container host over SSH")
class SetupHostOverSsh(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("docker is not installed")
        work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (work / "image").mkdir()
        (work / "image" / "Dockerfile").write_text(HOST_DOCKERFILE)
        docker("build", "-q", "-t", HOST_IMAGE, str(work / "image"))
        self.name = f"pakem-setup-host-{os.getpid()}"
        docker(
            "run", "-d", "--name", self.name, "--privileged", "--cgroupns=private",
            "-p", "127.0.0.1::22", "-v", "/var/lib/docker", "-v", "/var/lib/containerd",
            HOST_IMAGE,
        )
        self.addCleanup(docker, "rm", "--force", "--volumes", self.name, check=False)
        self.addCleanup(docker, "rmi", HOST_IMAGE, check=False)
        port = docker("port", self.name, "22").stdout.strip().splitlines()[0].rsplit(":", 1)[1]

        self.home = work / "home"
        ssh_dir = self.home / ".ssh"
        ssh_dir.mkdir(parents=True, mode=0o700)
        key = ssh_dir / "admin_login"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        public = key.with_suffix(".pub").read_text()
        docker("exec", self.name, "bash", "-c",
               f"install -d -m 700 -o admin -g admin ~admin/.ssh && echo '{public.strip()}' > ~admin/.ssh/authorized_keys "
               "&& chown admin:admin ~admin/.ssh/authorized_keys && chmod 600 ~admin/.ssh/authorized_keys")
        (ssh_dir / "config").write_text(
            f"Host pakem-sandbox\n  HostName 127.0.0.1\n  Port {port}\n  User admin\n  IdentityFile {key}\n"
            f"  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n  UserKnownHostsFile {ssh_dir}/known_hosts\n"
        )
        deadline = time.monotonic() + 60
        while subprocess.run(["ssh", "-F", str(ssh_dir / "config"), "pakem-sandbox", "true"],
                             capture_output=True).returncode != 0:
            self.assertLess(time.monotonic(), deadline, "sshd did not come up")
            time.sleep(2)
        # OpenSSH reads ~/.ssh/config from the passwd home, not $HOME, so the test points
        # the real ssh at its own config through a wrapper.
        wrapper = work / "bin" / "ssh"
        wrapper.parent.mkdir()
        wrapper.write_text(f'#!/bin/sh\nexec /usr/bin/ssh -F "{ssh_dir}/config" "$@"\n')
        wrapper.chmod(0o755)
        self.env = isolated_env(self.home, {"PATH": f"{wrapper.parent}:{os.environ.get('PATH', '/usr/bin:/bin')}"})

    def pakem(self, *args: str, timeout: float = 900) -> tuple[int, str]:
        argv = [sys.executable, str(PAKEM), "--root", str(REPO_ROOT), *args]
        return run_tty(argv, self.env, timeout)

    def test_setup_twice_then_check(self) -> None:
        # The checkout under test may be a branch; a release runs from merged main.
        waivers = ("--allow-unmerged", "--allow-dirty")
        code, first = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--yes", *waivers)
        self.assertEqual(code, 0, first[-4000:])
        self.assertIn("host ready", first)
        code, again = self.pakem("setup", "host", "pakem-sandbox", "--name", "sandbox", "--yes", *waivers)
        self.assertEqual(code, 0, again[-4000:])
        self.assertIn("docker present", again)
        self.assertNotIn("reloading systemd", again)
        code, checked = self.pakem("setup", "check", "--host", "sandbox", timeout=300)
        self.assertEqual(code, 0, checked[-4000:])
        self.assertIn("host ready", checked)
        leftovers = docker("exec", self.name, "bash", "-c", "ls -d /tmp/pakem-host.* 2>/dev/null | wc -l").stdout
        self.assertEqual(leftovers.strip(), "0", "every upload is removed")


if __name__ == "__main__":
    unittest.main()
