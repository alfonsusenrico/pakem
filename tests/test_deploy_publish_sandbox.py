"""CI publishing against a real registry with docker, oras, and cosign (opt-in: AF_SANDBOX=1).

af_publish.py publishes, promotes, and rolls back a tiny project into `registry:3` in
local mode (plain http, single-architecture build); af_release.py, the host side, then
fetches and verifies each environment pointer. Everything is removed afterwards.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_oci  # noqa: E402
import af_publish  # noqa: E402
import af_release  # noqa: E402

ENABLED = os.environ.get("AF_SANDBOX") == "1"
COMMIT = "4" * 40
RELEASE_TOML = textwrap.dedent(
    """\
    af_standard = 1
    project = "publish-sandbox"
    compose_files = ["compose.yaml"]
    migrate = []

    [health]
    service = "web"
    path = "/health"
    revision_key = "revision"
    port = 8000

    [[images]]
    name = "web"

    [environments.staging]
    keep = 3

    [environments.production]
    keep = 6
    """
)
SETTINGS_TOML = 'af_standard = 1\nrequired = ["HTTP_PORT"]\nplacement = ["HTTP_PORT"]\n'


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(ENABLED, "set AF_SANDBOX=1 to publish into a real registry")
class PublishToRealRegistry(unittest.TestCase):
    def setUp(self) -> None:
        for tool in ("docker", "oras", "cosign"):
            if shutil.which(tool) is None:
                self.skipTest(f"{tool} is not installed")
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        port = free_port()
        name = f"af-publish-sandbox-{port}"
        subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5000", "registry:3"],
                       capture_output=True, check=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True, check=False)
        self.repository = f"127.0.0.1:{port}/owner/publish-sandbox"
        self.addCleanup(self.remove_images)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        keys = self.work / "keys"
        keys.mkdir()
        subprocess.run(["cosign", "generate-key-pair"], cwd=keys, env={**os.environ, "COSIGN_PASSWORD": ""},
                       capture_output=True, check=True)
        self.public_key = keys / "cosign.pub"
        os.environ["COSIGN_PRIVATE_KEY"] = (keys / "cosign.key").read_text()
        os.environ["COSIGN_PASSWORD"] = ""
        self.addCleanup(os.environ.pop, "COSIGN_PRIVATE_KEY")
        self.addCleanup(os.environ.pop, "COSIGN_PASSWORD")
        self.root = self.work / "project"
        (self.root / "deploy").mkdir(parents=True)
        (self.root / "deploy/release.toml").write_text(RELEASE_TOML)
        (self.root / "deploy/settings.toml").write_text(SETTINGS_TOML)
        (self.root / "compose.yaml").write_text("services:\n  web:\n    image: ${AF_IMAGE_WEB}\n")
        (self.root / "Dockerfile").write_text("FROM python:3.13-alpine\nARG AF_REVISION\nRUN printf '%s' \"$AF_REVISION\" > /revision\n")

    def remove_images(self) -> None:
        listing = subprocess.run(["docker", "images", "--quiet", "--filter", f"reference={self.repository}/*"],
                                 capture_output=True, text=True, check=False)
        for image in sorted(set(listing.stdout.split())):
            subprocess.run(["docker", "rmi", "-f", image], capture_output=True, check=False)

    def fetch(self, environment: str) -> af_release.Release:
        verify = af_release.CosignVerifier(self.public_key)
        pointer = f"{self.repository}:release-{environment}"
        return af_release.fetch_release(af_oci.Registry(), pointer, "publish-sandbox", verify)

    def test_publish_promote_and_rollback_are_verified_by_the_host_side(self) -> None:
        publisher = af_publish.Publisher(self.repository, local=True, work=self.work / "publish")
        (self.work / "publish").mkdir()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            staging = publisher.publish(self.root, COMMIT, "https://example.test/owner/p", "https://ci.test/1")
        finally:
            os.chdir(cwd)
        fetched = self.fetch("staging")
        self.assertEqual(fetched.record, staging)
        config = af_release.fetch_bundle(af_oci.Registry(), fetched.record, self.work / "bundle")
        self.assertEqual(config["project"], "publish-sandbox")

        production = publisher.promote(COMMIT, "v1.0.0", "https://ci.test/2", self.public_key)
        fetched = self.fetch("production")
        self.assertEqual(fetched.record, production)
        self.assertEqual(fetched.record["images"], staging["images"])

        rolled = publisher.rollback(COMMIT, "production", "https://ci.test/3", self.public_key)
        self.assertTrue(self.fetch("production").record["rollback"])
        self.assertEqual(rolled["version"], "v1.0.0")

        other = af_publish.Publisher(self.repository, local=True, work=self.work / "other")
        (self.work / "other").mkdir()
        os.environ["COSIGN_PRIVATE_KEY"] = "not a key"
        with self.assertRaises(af_publish.PublishError):
            other.sign(staging["bundle"])


if __name__ == "__main__":
    unittest.main()
