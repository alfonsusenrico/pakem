"""Engine fetch stage against a real registry, oras, and cosign (opt-in: AF_SANDBOX=1).

Starts `registry:3` on a loopback port, publishes a release the way CI will (oras push
with artifact types, cosign key signatures without a transparency log), then checks that
af_release accepts it and refuses a key that did not sign it. Everything is removed after.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_oci  # noqa: E402
import af_release  # noqa: E402

ENABLED = os.environ.get("AF_SANDBOX") == "1"
EXAMPLES = DEPLOY_ASSETS / "contracts"
REGISTRY_IMAGE = "registry:3"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run(*arguments: str, cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"{arguments[0]} {arguments[1]} failed: {result.stderr[-400:]}")
    return result.stdout + result.stderr


def deterministic_tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mode = 0o755 if name.startswith("deploy/hooks/") else 0o644
            archive.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue()


@unittest.skipUnless(ENABLED, "set AF_SANDBOX=1 to run the registry and cosign sandbox")
class RealRegistryRelease(unittest.TestCase):
    def setUp(self) -> None:
        for tool in ("docker", "oras", "cosign"):
            if shutil.which(tool) is None:
                self.skipTest(f"{tool} is not installed")
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        port = free_port()
        self.container = f"af-engine-sandbox-{port}"
        run("docker", "run", "-d", "--name", self.container, "-p", f"127.0.0.1:{port}:5000", REGISTRY_IMAGE, cwd=self.work)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.container], capture_output=True, check=False)
        self.repository = f"127.0.0.1:{port}/owner/example"
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.work), "COSIGN_PASSWORD": ""}
        run("cosign", "generate-key-pair", cwd=self.work, env=self.env)
        run("cosign", "signing-config", "create", "--out", "signing-config.json", cwd=self.work, env=self.env)

    def push(self, tag: str, artifact_type: str, name: str, content: bytes) -> str:
        (self.work / name).write_bytes(content)
        output = run(
            "oras", "push", "--plain-http", f"{self.repository}:{tag}", "--artifact-type", artifact_type,
            f"{name}:{artifact_type}", cwd=self.work, env=self.env,
        )
        return next(line.split()[-1] for line in output.splitlines() if line.startswith("Digest:"))

    def sign(self, digest: str) -> None:
        run(
            "cosign", "sign", "--yes", "--key", "cosign.key", "--signing-config", "signing-config.json",
            "--allow-http-registry", f"{self.repository}@{digest}", cwd=self.work, env=self.env,
        )

    def test_published_release_is_fetched_and_verified(self) -> None:
        bundle = deterministic_tar(
            {
                "compose.yaml": b"services: {}\n",
                "deploy/release.toml": (EXAMPLES / "release.toml").read_bytes(),
                "deploy/settings.toml": (EXAMPLES / "settings.toml").read_bytes(),
                "deploy/env/production.env": (EXAMPLES / "production.env").read_bytes(),
                "deploy/hooks/backup": b"#!/bin/sh\nexit 0\n",
            }
        )
        digests = {
            "web": self.push("img-web", "application/x-test-image", "web.bin", b"web"),
            "worker": self.push("img-worker", "application/x-test-image", "worker.bin", b"worker"),
            "bundle": self.push("bundle-test", af_release.BUNDLE_MEDIA, "bundle.tar", bundle),
        }
        record = json.loads((EXAMPLES / "release-record.staging.json").read_text())
        record["images"] = {name: f"{self.repository}@{digests[name]}" for name in ("web", "worker")}
        record["bundle"] = f"{self.repository}@{digests['bundle']}"
        record_digest = self.push("release-staging", af_release.RELEASE_MEDIA, "record.json", json.dumps(record).encode())
        for digest in (record_digest, *digests.values()):
            self.sign(digest)

        verify = af_release.CosignVerifier(self.work / "cosign.pub")
        pointer = f"{self.repository}:release-staging"
        release = af_release.fetch_release(af_oci.Registry(), pointer, "example", verify)
        self.assertEqual(release.digest, record_digest)
        config = af_release.fetch_bundle(af_oci.Registry(), release.record, self.work / "unpacked")
        self.assertEqual(config["project"], "example")
        self.assertTrue((self.work / "unpacked/deploy/hooks/backup").stat().st_mode & 0o100)

        other = self.work / "other"
        other.mkdir()
        run("cosign", "generate-key-pair", cwd=other, env=self.env)
        with self.assertRaisesRegex(af_release.ReleaseError, "signature check failed"):
            af_release.fetch_release(af_oci.Registry(), pointer, "example", af_release.CosignVerifier(other / "cosign.pub"))

        unsigned = self.push("release-staging", af_release.RELEASE_MEDIA, "record2.json", json.dumps(dict(record, rollback=True)).encode())
        self.assertNotEqual(unsigned, record_digest)
        with self.assertRaisesRegex(af_release.ReleaseError, "signature check failed"):
            af_release.fetch_release(af_oci.Registry(), pointer, "example", verify)


if __name__ == "__main__":
    unittest.main()
