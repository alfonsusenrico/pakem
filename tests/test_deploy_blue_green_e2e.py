"""Blue-green switching end to end with real Docker, registry, and cosign (opt-in: AF_SANDBOX=1).

A client requests the release through the route file the engine writes, as the host proxy
does, without pause while the engine upgrades a blue-green project: no request may fail,
the answers move from the old commit to the new one exactly once, and only the new
container is left. Everything is removed afterwards.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import unittest

from engine_sandbox import COMPOSE, ENABLED, RELEASE_TOML, SKIP_REASON, EngineSandbox
from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402

BLUE_GREEN_COMPOSE = COMPOSE.replace('ports: ["127.0.0.1:${HTTP_PORT}:8000"]', 'ports: ["127.0.0.1::8000"]')
BLUE_GREEN_RELEASE = RELEASE_TOML.replace(
    "drain_seconds = 5\n",
    'drain_seconds = 5\nswitch = "blue-green"\n\n[[routes]]\nhost_setting = "PUBLIC_HOST"\nservice = "web"\nport = 8000\n',
)
UPSTREAM = re.compile(r"reverse_proxy 127\.0\.0\.1:(\d+)\n")


class RouteClient(threading.Thread):
    """Requests the health path through whatever port the route file names, until stopped."""

    def __init__(self, route_file, probe) -> None:
        super().__init__(daemon=True)
        self.route_file = route_file
        self.probe = probe
        self.halt = threading.Event()
        self.answers: list[str] = []
        self.requests = 0
        self.failures = 0

    def run(self) -> None:
        while not self.halt.is_set():
            port = UPSTREAM.search(self.route_file.read_text()).group(1)
            body = self.probe(f"http://127.0.0.1:{port}/health/release")
            self.requests += 1
            if body is None:
                self.failures += 1
            elif not self.answers or self.answers[-1] != body["revision"]:
                self.answers.append(body["revision"])


@unittest.skipUnless(ENABLED, SKIP_REASON)
class BlueGreenEndToEnd(EngineSandbox):
    def test_an_upgrade_never_drops_a_request(self) -> None:
        old, new = "7" * 40, "8" * 40
        config = self.make_host("production")
        sites = self.work / "sites"
        sites.mkdir()
        config.proxy = af_engine.ProxyConfig(sites, ["true"], tls=False)
        files = {"compose": BLUE_GREEN_COMPOSE, "release_toml": BLUE_GREEN_RELEASE}

        self.publish(old, "2026-10-20T00:00:00Z", **files)
        self.assertEqual(self.deploy(config)["result"], "deployed", self.last_output)
        client = RouteClient(sites / "sandbox-production.caddy", af_engine.probe_health)
        client.start()
        self.publish(new, "2026-10-21T00:00:00Z", **files)
        record = self.deploy(config)
        client.halt.set()
        client.join(timeout=10)

        self.assertEqual(record["result"], "deployed", self.last_output)
        self.assertGreater(client.requests, 100)
        self.assertEqual(client.failures, 0, f"{client.failures} of {client.requests} requests failed")
        self.assertEqual(client.answers, [old, new])
        running = subprocess.run(
            ["docker", "ps", "--quiet", "--filter", "label=com.docker.compose.project=sandbox-production"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertEqual(len(running), 1)


if __name__ == "__main__":
    unittest.main()
