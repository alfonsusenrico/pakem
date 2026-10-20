"""Proxy routes against a real Caddy (opt-in: AF_SANDBOX=1).

Runs Caddy with the shipped host Caddyfile (admin API on a unix socket), lets af_engine write
a project's route file and reload Caddy through that socket, and checks that traffic for the
domain reaches the upstream, that a configuration Caddy refuses is put back while the old
routes keep serving, and that the admin socket is not open to other users.
"""

from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402

ENABLED = os.environ.get("AF_SANDBOX") == "1"
CADDY_IMAGE = "caddy:2.11.7@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af"
UPSTREAM = ":9000 {\n\trespond \"upstream answered\"\n}\n"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(ENABLED, "set AF_SANDBOX=1 to reload a real Caddy")
class CaddyRoutes(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("docker is not installed")
        work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sites = work / "sites"
        self.sites.mkdir()
        (self.sites / "upstream.caddy").write_text(UPSTREAM)
        self.port = free_port()
        self.name = f"af-proxy-sandbox-{self.port}"
        subprocess.run(
            [
                "docker", "run", "-d", "--name", self.name, "-p", f"127.0.0.1:{self.port}:80",
                "-v", f"{DEPLOY_ASSETS / 'caddy' / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
                "-v", f"{self.sites}:/etc/caddy/sites",
                CADDY_IMAGE, "sh", "-c", "mkdir -p /run/caddy && exec caddy run --config /etc/caddy/Caddyfile",
            ],
            capture_output=True, check=True,
        )
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.name], capture_output=True, check=False)
        for _ in range(50):
            if self.exec("test", "-S", "/run/caddy/admin.sock").returncode == 0:
                break
            time.sleep(0.1)
        release_dir = work / "release"
        release_dir.mkdir()
        (release_dir / "runtime.env").write_text("PUBLIC_HOST='example.test'\n")
        self.release = af_engine.Release(
            release_dir, {"commit": "6" * 40}, {"routes": [{"host_setting": "PUBLIC_HOST", "service": "web", "port": 8000}]}
        )
        reload = ["docker", "exec", self.name, "caddy", "reload", "--config", "/etc/caddy/Caddyfile",
                  "--address", "unix//run/caddy/admin.sock"]
        config = af_engine.HostConfig(
            project="example", environment="staging", base=work / "base", pointer="ghcr.io/o/example:release-staging",
            public_key=work / "unused.pub", proxy=af_engine.ProxyConfig(self.sites, reload, tls=False),
        )
        # The upstream is Caddy's own :9000 site; Docker's answers for the web container point there.
        def query(arguments: list[str], env: dict) -> tuple[int, str]:
            return 0, "web-container\n" if arguments[1] == "ps" else "127.0.0.1:9000\n"

        self.engine = af_engine.Engine(config, verify=lambda reference: None, query=query)

    def exec(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", "exec", self.name, *arguments], capture_output=True, text=True, check=False)

    def get(self, host: str) -> str:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/", headers={"Host": host})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            error.close()
            return f"HTTP {error.code}"
        except OSError:
            return "unreachable"

    def test_routes_reload_serve_and_survive_a_refused_change(self) -> None:
        self.assertEqual(self.exec("stat", "-c", "%a", "/run/caddy/admin.sock").stdout.strip(), "660")
        self.assertEqual(self.get("example.test"), "unreachable")  # no site on port 80 yet

        output = io.StringIO()
        with redirect_stdout(output):
            text = self.engine.route_text(self.release, self.engine.route_domains(self.release), {})
            self.assertTrue(self.engine.write_routes(text))
            self.assertEqual(self.get("example.test"), "upstream answered")
            good = (self.sites / "example-staging.caddy").read_text()
            # A file Caddy cannot parse is put back and the old routes keep serving.
            self.assertFalse(self.engine.write_routes(good.replace("reverse_proxy", "reverse_proxy {")))
        self.assertEqual((self.sites / "example-staging.caddy").read_text(), good)
        self.assertEqual(self.get("example.test"), "upstream answered")


if __name__ == "__main__":
    unittest.main()
