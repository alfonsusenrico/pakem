"""CI publishing (af_publish.py): bundle, schema version, records, promotion, rollback."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_publish  # noqa: E402
import contracts  # noqa: E402

EXAMPLES = DEPLOY_ASSETS / "contracts"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY = "ghcr.io/owner/example"


def project(case: unittest.TestCase) -> Path:
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    (root / "deploy/env").mkdir(parents=True)
    (root / "deploy/hooks").mkdir()
    shutil.copy(EXAMPLES / "release.toml", root / "deploy/release.toml")
    shutil.copy(EXAMPLES / "settings.toml", root / "deploy/settings.toml")
    shutil.copy(EXAMPLES / "production.env", root / "deploy/env/production.env")
    (root / "deploy/hooks/backup").write_text("#!/bin/sh\nexit 0\n")
    (root / "deploy/hooks/backup").chmod(0o755)
    (root / "compose.yaml").write_text("services: {}\n")
    (root / "src/app/migrations").mkdir(parents=True)
    for name in ("0001_initial.sql", "0010_accounts.sql", "0002_users.sql"):
        (root / "src/app/migrations" / name).write_text("-- sql\n")
    (root / "src/app/main.py").write_text("print('not in the bundle')\n")
    return root


class FakeTools:
    """Pretends to be docker, oras, and cosign; stores pushed artifacts by tag."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.artifacts: dict[str, bytes] = {}
        self.counter = 0

    def digest(self) -> str:
        self.counter += 1
        return "sha256:" + f"{self.counter:064x}"

    def __call__(self, arguments: list[str], cwd: Path | None) -> str:
        self.calls.append(arguments)
        tool, command = arguments[0], arguments[1]
        if tool == "docker" and command == "buildx":
            metadata = Path(arguments[arguments.index("--metadata-file") + 1])
            metadata.write_text(json.dumps({"containerimage.digest": self.digest()}))
            return ""
        if tool == "oras" and command == "push":
            target = next(a for a in arguments if a.startswith(REPOSITORY + ":"))
            name = arguments[-1].split(":")[0]
            content = (cwd / name).read_bytes()
            for tag in target.split(":", 1)[1].split(","):
                self.artifacts[tag] = content
            return f"Pushed {target}\nDigest: {self.digest()}\n"
        if tool == "oras" and command == "pull":
            tag = arguments[2].split(":", 1)[1]
            if tag not in self.artifacts:
                raise af_publish.PublishError(f"oras pull failed:\nError response from registry: {arguments[2]}: not found")
            output = Path(arguments[arguments.index("--output") + 1])
            (output / "record.json").write_bytes(self.artifacts[tag])
            return f"Pulled\nDigest: {self.digest()}\n"
        return ""

    def tools(self, name: str, command: str | None = None) -> list[list[str]]:
        return [call for call in self.calls if call[0] == name and (command is None or call[1] == command)]


class Bundle(unittest.TestCase):
    def test_bundle_is_reproducible_and_holds_only_deploy_files(self) -> None:
        root = project(self)
        config = af_publish.load_config(root)
        first = af_publish.build_bundle(root, config)
        os.utime(root / "compose.yaml", (1, 1))
        self.assertEqual(af_publish.build_bundle(root, config), first)
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            members = {member.name: member for member in archive.getmembers()}
        self.assertEqual(
            sorted(members),
            ["compose.yaml", "deploy/env/production.env", "deploy/hooks/backup", "deploy/release.toml", "deploy/settings.toml"],
        )
        self.assertEqual(members["deploy/hooks/backup"].mode, 0o755)
        self.assertEqual({m.mtime for m in members.values()} | {m.uid for m in members.values()}, {0})

    def test_bytecode_caches_are_left_out(self) -> None:
        root = project(self)
        config = af_publish.load_config(root)
        clean = af_publish.build_bundle(root, config)
        (root / "deploy/af/__pycache__").mkdir(parents=True)
        (root / "deploy/af/__pycache__/contracts.cpython-312.pyc").write_bytes(b"cache")
        (root / "deploy/hooks/helper.pyc").write_bytes(b"cache")
        self.assertEqual(af_publish.build_bundle(root, config), clean)

    def test_local_and_secret_files_never_enter_a_bundle(self) -> None:
        for name in ("deploy/.env.production", "deploy/._release.toml", "deploy/.DS_Store"):
            root = project(self)
            (root / name).write_text("x")
            with self.subTest(name=name), self.assertRaises(af_publish.PublishError):
                af_publish.build_bundle(root, af_publish.load_config(root))

    def test_schema_version_is_the_newest_migration(self) -> None:
        root = project(self)
        self.assertEqual(af_publish.schema_version(root, af_publish.load_config(root)), "0010_accounts")
        for path in (root / "src/app/migrations").iterdir():
            path.unlink()
        with self.assertRaises(af_publish.PublishError):
            af_publish.schema_version(root, af_publish.load_config(root))


class Publishing(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["COSIGN_PRIVATE_KEY"] = "test-key-material"
        self.addCleanup(os.environ.pop, "COSIGN_PRIVATE_KEY")
        self.tools = FakeTools()
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.publisher = af_publish.Publisher(REPOSITORY, runner=self.tools, work=self.work)

    def test_publish_builds_once_signs_everything_and_moves_staging(self) -> None:
        record = self.publisher.publish(project(self), COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")
        self.assertEqual(contracts.validate_release_record(record), [])
        builds = self.tools.tools("docker", "buildx")
        self.assertEqual(len(builds), 2)
        self.assertIn("linux/amd64,linux/arm64", builds[0])
        self.assertIn(f"org.opencontainers.image.revision={COMMIT}", builds[0])
        signed = [call[-1] for call in self.tools.tools("cosign", "sign")]
        self.assertEqual(len(signed), 4)  # two images, the bundle, the record
        self.assertTrue(all("@sha256:" in reference for reference in signed))
        self.assertTrue(all("env://COSIGN_PRIVATE_KEY" in call for call in self.tools.tools("cosign", "sign")))
        self.assertEqual(json.loads(self.tools.artifacts["release-staging"]), record)
        self.assertEqual(self.tools.artifacts[f"record-{COMMIT}"], self.tools.artifacts["release-staging"])
        self.assertEqual(record["schema_version"], "0010_accounts")
        self.assertFalse(any("test-key-material" in " ".join(call) for call in self.tools.calls))

    def test_an_image_target_builds_that_stage(self) -> None:
        root = project(self)
        config = (root / "deploy/release.toml").read_text()
        (root / "deploy/release.toml").write_text(
            config.replace('dockerfile = "Dockerfile.worker"', 'dockerfile = "Dockerfile"\ntarget = "worker"')
        )
        self.publisher.publish(root, COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")
        web, worker = self.tools.tools("docker", "buildx")
        self.assertNotIn("--target", web)
        self.assertEqual(worker[worker.index("--target") + 1], "worker")
        self.assertEqual(worker[worker.index("--file") + 1], str(root / "Dockerfile"))

    def test_promotion_reuses_the_verified_staging_record(self) -> None:
        staging = self.publisher.publish(project(self), COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")
        builds_before = len(self.tools.tools("docker"))
        production = self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"))
        self.assertEqual(len(self.tools.tools("docker")), builds_before)
        self.assertEqual(self.tools.tools("cosign", "verify")[0][3], "release.pub")
        self.assertEqual(contracts.validate_promotion(staging, production), [])
        self.assertEqual(json.loads(self.tools.artifacts["release-production"])["version"], "v1.2.0")
        self.assertIn(f"record-{COMMIT}-production", self.tools.artifacts)

    def test_promotion_without_a_staging_record_fails(self) -> None:
        with self.assertRaisesRegex(af_publish.PublishError, "was not published"):
            self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"))

    def test_promotion_waits_for_a_release_still_being_published(self) -> None:
        now = [0.0]
        root = project(self)

        def sleep(seconds: float) -> None:
            now[0] += seconds
            if now[0] >= 90:  # the merge's publish finishes while the tag waits
                self.publisher.sleep = lambda seconds: None
                self.publisher.publish(root, COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")

        self.publisher.sleep, self.publisher.clock = sleep, lambda: now[0]
        production = self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"), 600)
        self.assertEqual(production["version"], "v1.2.0")
        self.assertEqual(now[0], 90)

    def test_promotion_gives_up_after_the_wait_and_never_retries_other_errors(self) -> None:
        now = [0.0]
        self.publisher.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        self.publisher.clock = lambda: now[0]
        with self.assertRaisesRegex(af_publish.PublishError, "was not published"):
            self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"), 120)
        self.assertEqual(now[0], 120)

        def refuse(arguments: list[str], cwd: Path | None) -> str:
            raise af_publish.PublishError("oras pull failed:\nunauthorized: authentication required")

        calls = []
        self.publisher.runner = lambda arguments, cwd: calls.append(arguments) or refuse(arguments, cwd)
        with self.assertRaisesRegex(af_publish.PublishError, "unauthorized"):
            self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"), 600)
        self.assertEqual(len(calls), 1)

    def test_rollback_marks_the_record_and_moves_only_that_environment(self) -> None:
        self.publisher.publish(project(self), COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")
        self.publisher.promote(COMMIT, "v1.2.0", "https://ci.test/run/2", Path("release.pub"))
        record = self.publisher.rollback(COMMIT, "production", "https://ci.test/run/3", Path("release.pub"))
        self.assertTrue(record["rollback"])
        self.assertEqual(record["version"], "v1.2.0")
        self.assertTrue(json.loads(self.tools.artifacts["release-production"])["rollback"])
        self.assertFalse(json.loads(self.tools.artifacts["release-staging"])["rollback"])

    def test_signing_needs_the_key_in_the_environment(self) -> None:
        del os.environ["COSIGN_PRIVATE_KEY"]
        with self.assertRaisesRegex(af_publish.PublishError, "COSIGN_PRIVATE_KEY"):
            self.publisher.publish(project(self), COMMIT, "https://github.com/owner/example", "https://ci.test/run/1")
        os.environ["COSIGN_PRIVATE_KEY"] = "test-key-material"

    def test_command_line_validates_inputs(self) -> None:
        self.assertEqual(af_publish.main(["promote", "--repository", REPOSITORY, "--commit", "abc", "--ci-run", "x",
                                          "--public-key", "k", "--version", "v1.0.0"]), 1)
        with self.assertRaises(af_publish.PublishError):
            af_publish.Publisher("GHCR.io/Owner/Example")


if __name__ == "__main__":
    unittest.main()
