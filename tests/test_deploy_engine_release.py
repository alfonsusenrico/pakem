"""Release engine, fetch stage: registry client, pointer, signatures, bundle."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from registry_fake import FakeRegistry, digest_of
from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_oci  # noqa: E402
import af_release  # noqa: E402

EXAMPLES = DEPLOY_ASSETS / "contracts"
REPO = "owner/example"


def tar_bytes(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if name.startswith("deploy/hooks/") else 0o644
            archive.addfile(info, io.BytesIO(content))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return buffer.getvalue()


def bundle_files() -> dict[str, bytes]:
    return {
        "compose.yaml": b"services: {}\n",
        "deploy/release.toml": (EXAMPLES / "release.toml").read_bytes(),
        "deploy/settings.toml": (EXAMPLES / "settings.toml").read_bytes(),
        "deploy/env/production.env": (EXAMPLES / "production.env").read_bytes(),
        "deploy/hooks/backup": b"#!/bin/sh\nexit 0\n",
    }


class Signatures:
    """Records verified references; refuses any listed as unsigned."""

    def __init__(self) -> None:
        self.checked: list[str] = []
        self.unsigned: set[str] = set()

    def __call__(self, reference: str) -> None:
        self.checked.append(reference)
        if any(reference.endswith(digest) for digest in self.unsigned):
            raise af_release.ReleaseError("signature check failed")


class Published:
    """A registry holding one release: images, bundle, record, and a staging pointer."""

    def __init__(self, registry: FakeRegistry, *, bundle: bytes | None = None, record_changes: dict | None = None) -> None:
        self.bundle_digest = registry.push_artifact(REPO, None, af_release.BUNDLE_MEDIA, bundle or tar_bytes(bundle_files()))
        self.images = {
            name: f"{registry.host}/{REPO}@" + registry.push_artifact(REPO, None, "application/x-test-image", name.encode())
            for name in ("web", "worker")
        }
        record = json.loads((EXAMPLES / "release-record.staging.json").read_text())
        record.update(images=self.images, bundle=f"{registry.host}/{REPO}@{self.bundle_digest}")
        record.update(record_changes or {})
        self.record = record
        self.record_digest = registry.push_artifact(
            REPO, "release-staging", af_release.RELEASE_MEDIA, json.dumps(record).encode()
        )
        self.pointer = f"{registry.host}/{REPO}:release-staging"


class ReferenceParsing(unittest.TestCase):
    def test_tags_and_digests(self) -> None:
        tagged = af_oci.Reference.parse("ghcr.io/owner/example:release-staging")
        self.assertEqual((tagged.registry, tagged.repository, tagged.tag), ("ghcr.io", "owner/example", "release-staging"))
        pinned = af_oci.Reference.parse("127.0.0.1:5000/owner/example@sha256:" + "a" * 64)
        self.assertEqual(pinned.digest, "sha256:" + "a" * 64)
        for bad in ("ghcr.io/owner/example", "owner/example:tag", "ghcr.io/Owner/x:tag", "ghcr.io/o/x@sha256:abc"):
            with self.subTest(bad=bad), self.assertRaises(af_oci.RegistryError):
                af_oci.Reference.parse(bad)


class FetchRelease(unittest.TestCase):
    def test_pointer_resolves_to_a_verified_valid_record(self) -> None:
        with FakeRegistry() as registry:
            published = Published(registry)
            signatures = Signatures()
            release = af_release.fetch_release(af_oci.Registry(), published.pointer, "example", signatures)
        self.assertEqual(release.digest, published.record_digest)
        self.assertEqual(release.record, published.record)
        expected = [f"{registry.host}/{REPO}@{published.record_digest}", *published.images.values(), published.record["bundle"]]
        self.assertEqual(signatures.checked, expected)

    def test_any_unsigned_digest_refuses_the_release(self) -> None:
        with FakeRegistry() as registry:
            published = Published(registry)
            for digest in (published.record_digest, published.bundle_digest, published.images["worker"].split("@")[1]):
                signatures = Signatures()
                signatures.unsigned.add(digest)
                with self.subTest(digest=digest), self.assertRaises(af_release.ReleaseError):
                    af_release.fetch_release(af_oci.Registry(), published.pointer, "example", signatures)

    def test_invalid_or_foreign_records_are_refused(self) -> None:
        with FakeRegistry() as registry:
            unpinned = Published(registry, record_changes={"images": {"web": "ghcr.io/owner/example:latest"}})
            with self.assertRaisesRegex(af_release.ReleaseError, "pinned by digest"):
                af_release.fetch_release(af_oci.Registry(), unpinned.pointer, "example", Signatures())
            published = Published(registry)
            with self.assertRaisesRegex(af_release.ReleaseError, "another project"):
                af_release.fetch_release(af_oci.Registry(), published.pointer, "other", Signatures())
            with self.assertRaisesRegex(af_release.ReleaseError, "must be a tag"):
                pinned = f"{registry.host}/{REPO}@{published.record_digest}"
                af_release.fetch_release(af_oci.Registry(), pinned, "example", Signatures())

    def test_tampered_content_is_detected(self) -> None:
        with FakeRegistry() as registry:
            published = Published(registry)
            record_manifest = json.loads(registry.manifests[published.record_digest])
            registry.tamper.add(record_manifest["layers"][0]["digest"])
            with self.assertRaisesRegex(af_release.ReleaseError, "does not match"):
                af_release.fetch_release(af_oci.Registry(), published.pointer, "example", Signatures())

    def test_registry_credentials_are_used_only_for_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credentials = Path(directory) / "registries.json"
            with FakeRegistry(username="reader", password="read-only-token") as registry:
                published = Published(registry)
                credentials.write_text(json.dumps({registry.host: {"username": "reader", "token": "read-only-token"}}))
                credentials.chmod(0o600)
                client = af_oci.Registry(af_oci.credentials_file(credentials))
                af_release.fetch_release(client, published.pointer, "example", Signatures())
                with self.assertRaises(af_release.ReleaseError):
                    af_release.fetch_release(af_oci.Registry(), published.pointer, "example", Signatures())
                credentials.chmod(0o644)
                with self.assertRaisesRegex(af_release.ReleaseError, "readable by others"):
                    af_release.fetch_release(af_oci.Registry(af_oci.credentials_file(credentials)), published.pointer, "example", Signatures())


class Ordering(unittest.TestCase):
    def test_older_releases_need_the_rollback_flag(self) -> None:
        current = json.loads((EXAMPLES / "release-record.production.json").read_text())
        older = dict(current, created="2026-01-01T00:00:00Z", commit="f" * 40)
        self.assertEqual(af_release.check_order(None, current), "deploy")
        self.assertEqual(af_release.check_order(current, dict(current)), "unchanged")
        with self.assertRaises(af_release.ReleaseError):
            af_release.check_order(current, older)
        self.assertEqual(af_release.check_order(current, dict(older, rollback=True)), "deploy")


class Bundle(unittest.TestCase):
    def fetch(self, files: dict[str, bytes] | None = None, *, raw: bytes | None = None) -> Path:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with FakeRegistry() as registry:
            published = Published(registry, bundle=raw or tar_bytes(files or bundle_files()))
            destination = directory / "bundle"
            af_release.fetch_bundle(af_oci.Registry(), published.record, destination)
        return destination

    def test_bundle_unpacks_privately_and_keeps_hook_executables(self) -> None:
        destination = self.fetch()
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((destination / "deploy/release.toml").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((destination / "deploy/hooks/backup").stat().st_mode), 0o700)

    def test_unsafe_bundles_are_refused(self) -> None:
        cases = {
            "escape": dict(bundle_files(), **{"../outside": b"x"}),
            "absolute": dict(bundle_files(), **{"/etc/cron.d/x": b"x"}),
            "env file": dict(bundle_files(), **{"deploy/.env.production": b"SECRET=1"}),
            "missing settings": {k: v for k, v in bundle_files().items() if k != "deploy/settings.toml"},
            "invalid config": dict(bundle_files(), **{"deploy/release.toml": b"af_standard = 2\n"}),
        }
        for name, files in cases.items():
            with self.subTest(case=name), self.assertRaises(af_release.ReleaseError):
                self.fetch(files)
        with self.assertRaisesRegex(af_release.ReleaseError, "only files and directories"):
            self.fetch(raw=tar_bytes(bundle_files(), symlink="deploy/link"))

    def test_bundle_images_must_match_the_record(self) -> None:
        files = bundle_files()
        files["deploy/release.toml"] = files["deploy/release.toml"].replace(b'name = "worker"', b'name = "jobs"')
        with self.assertRaisesRegex(af_release.ReleaseError, "record images differ"):
            self.fetch(files)


class Cosign(unittest.TestCase):
    def test_verifier_runs_cosign_on_digests_without_leaking_environment(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def run(arguments, env, **_kwargs):  # noqa: ANN001
            calls.append((arguments, env))
            return subprocess.CompletedProcess(arguments, 0)

        os.environ["AF_TEST_SECRET"] = "x"
        try:
            verify = af_release.CosignVerifier(Path("/etc/af/example.pub"), run=run)
            verify("ghcr.io/owner/example@sha256:" + "a" * 64)
        finally:
            del os.environ["AF_TEST_SECRET"]
        arguments, env = calls[0]
        self.assertEqual(arguments[:4], ["cosign", "verify", "--key", "/etc/af/example.pub"])
        self.assertIn("--insecure-ignore-tlog=true", arguments)
        self.assertNotIn("AF_TEST_SECRET", env)
        with self.assertRaises(af_release.ReleaseError):
            verify("ghcr.io/owner/example:latest")

    def test_failed_verification_raises(self) -> None:
        verify = af_release.CosignVerifier(Path("k.pub"), run=lambda a, **k: subprocess.CompletedProcess(a, 1))
        with self.assertRaisesRegex(af_release.ReleaseError, "signature check failed"):
            verify("ghcr.io/owner/example@sha256:" + "a" * 64)


if __name__ == "__main__":
    unittest.main()
