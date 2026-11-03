"""Direct delivery receiver: signed requests, builds, promotion, rollback, device keys.

Requests are signed with real `ssh-keygen -Y` keys; the engine is the real one over the
fake Docker of the engine sequence tests, and a fake builder stands in for BuildKit.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from support import DEPLOY_ASSETS
from test_deploy_engine_sequence import EXAMPLES, PLACEMENT, SECRET_VALUE, FakeDocker, RecordingNotifier

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_limits  # noqa: E402
import af_monitor  # noqa: E402
import af_receive  # noqa: E402
import af_settings  # noqa: E402
import af_trigger  # noqa: E402
import contracts  # noqa: E402

A, B, C = "a" * 40, "b" * 40, "c" * 40
DEVICE = "laptop"


def source_archive(commit: str, *, migration: str = "0001", extra: dict[str, bytes] | None = None) -> bytes:
    """A `git archive`-like tar of a project at one commit."""
    files = {
        "compose.yaml": b"services: {}\n",
        "Dockerfile": b"FROM scratch\n",
        "Dockerfile.worker": b"FROM scratch\n",
        "deploy/release.toml": (EXAMPLES / "release.toml").read_bytes(),
        "deploy/settings.toml": (EXAMPLES / "settings.toml").read_bytes(),
        "deploy/env/production.env": (EXAMPLES / "production.env").read_bytes(),
        "deploy/env/staging.env": b"LOG_LEVEL=debug\nSMTP_USER=\n",
        "deploy/hooks/backup": b"#!/bin/sh\nexit 0\n",
        f"src/app/migrations/{migration}_change.sql": b"-- migration\n",
        "README.md": f"commit {commit}\n".encode(),
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if "/hooks/" in name else 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeBuilder:
    def __init__(self) -> None:
        self.built: list[str] = []
        self.fails = False
        self.space: list[tuple[int | None, Path | None]] = []

    def build(self, source: Path, config: dict, project: str, commit: str, *,
              min_free_gb: int | None = None, base: Path | None = None) -> dict[str, str]:
        self.space.append((min_free_gb, base))
        if self.fails:
            raise af_receive.ReceiveError("building web failed", af_engine.EXIT_FAILED)
        self.built.append(commit)
        return {image["name"]: "sha256:" + hashlib.sha256(f"{image['name']}-{commit}".encode()).hexdigest()
                for image in config["images"]}


class DirectDocker(FakeDocker):
    """The fake Docker, plus the image checks and untagging of direct releases."""

    def __init__(self) -> None:
        super().__init__()
        self.missing: set[str] = set()

    def query(self, arguments: list[str], env: dict) -> tuple[int, str]:
        if arguments[:3] == ["docker", "image", "inspect"]:
            self.calls.append(("inspect", arguments[-1]))
            return (1, "") if arguments[-1] in self.missing else (0, f"{arguments[-1]}\n")
        return super().query(arguments, env)


class DirectHost(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_dir = self.root / "hosts"
        self.config_dir.mkdir()
        self.files = af_receive.KeyFiles(self.root / "af-ssh" / "authorized_keys", self.root / "allowed_signers")
        self.deploy_root = self.root / "deploy"
        self.deploy_root.mkdir()
        self.key = self.make_key("laptop-key")
        self.authorize(DEVICE, self.key, "example", ["staging", "production"])
        for environment in ("staging", "production"):
            base = self.deploy_root / "example" / environment
            (base / "placement.env").write_text(PLACEMENT)
            (base / "placement.env").chmod(0o600)
            af_settings.set_secret(base / "secrets", "DATABASE_PASSWORD", SECRET_VALUE.encode())
        self.docker = DirectDocker()
        self.builder = FakeBuilder()
        self.notifier = RecordingNotifier()

    def make_key(self, name: str) -> Path:
        path = self.root / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)], check=True)
        return path

    def public(self, key: Path) -> str:
        return " ".join(key.with_suffix(".pub").read_text().split()[:2])

    def authorize(self, device: str, key: Path, project: str, environments: list[str], **options) -> str:
        args = argparse.Namespace(
            device=device, key=self.public(key), project=project, environment=environments,
            deploy_root=str(self.deploy_root), proxy=options.get("proxy", False),
            switch_from_registry=options.get("switch_from_registry", False),
            placement=options.get("placement", []), secret=options.get("secret", []),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            af_receive.authorize(args, self.files, self.config_dir)
        return output.getvalue()

    def request(self, action: str, environment: str, commit: str, *, version: str | None = None,
                archive: bytes | None = None, key: Path | None = None, device: str = DEVICE,
                project: str = "example", created: datetime | None = None, allow_schema: bool = False) -> bytes:
        request = {
            "schema": contracts.REQUEST_SCHEMA, "action": action, "project": project, "environment": environment,
            "commit": commit, "version": version,
            "archive": f"sha256:{hashlib.sha256(archive).hexdigest()}" if archive is not None else None,
            "created": (created or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "device": device, "source": "https://example.test/owner/example", "allow_schema": allow_schema,
        }
        data = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        path = self.root / "request.json"
        path.write_bytes(data)
        signature = path.with_name("request.json.sig")
        signature.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key or self.key), "-n", af_receive.NAMESPACE, str(path)],
                       check=True, capture_output=True)
        return data + b"\n" + base64.b64encode(signature.read_bytes()) + b"\n" + (archive or b"")

    def engine(self, host, **options) -> af_engine.Engine:
        return af_engine.Engine(
            host, runner=self.docker, query=self.docker.query, health=self.docker.health,
            sleep=lambda seconds: None, health_attempts=2, notifier=self.notifier, **options,
        )

    def serve(self, command: str, stdin: bytes = b"", projects: tuple[str, ...] = ("example",)) -> tuple[int, str]:
        receiver = af_receive.Receiver(
            DEVICE, list(projects), config_dir=self.config_dir, signers=self.files.signers,
            builder=self.builder, engine=self.engine, notifier=lambda host: self.notifier,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = receiver.serve(command, io.BufferedReader(io.BytesIO(stdin)))
        return code, output.getvalue()

    def base(self, environment: str) -> Path:
        return self.deploy_root / "example" / environment

    def last(self, environment: str) -> dict:
        return json.loads((self.base(environment) / "deployment.json").read_text())

    def publish(self, commit: str, **archive_options) -> tuple[int, str]:
        return self.serve("release", self.request("publish", "staging", commit, archive=source_archive(commit, **archive_options)))


class ReleaseTests(DirectHost):
    def test_publish_builds_once_and_deploys_with_a_direct_record(self) -> None:
        code, output = self.publish(A)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.builder.built, [A])
        self.assertEqual(self.builder.space, [(None, self.base("staging"))],
                         "no min_free_gb in the host config: the host's own reserve applies in the build")
        deployment = self.last("staging")
        self.assertEqual((deployment["result"], deployment["trigger"], deployment["actor"]), ("deployed", "direct", "device:laptop"))
        self.assertEqual(contracts.validate_deployment_record(deployment), [])
        record = json.loads((self.base("staging") / "releases" / A / "record.json").read_text())
        self.assertEqual(contracts.validate_direct_record(record), [])
        self.assertEqual((record["device"], record["schema_version"], record["version"]), (DEVICE, "0001_change", None))
        self.assertEqual(deployment["release"], record["request"])
        self.assertIn(("inspect", record["images"]["web"]), self.docker.calls)
        self.assertNotIn("pull", self.docker.steps())
        self.assertFalse(any((self.base("staging") / "builds").iterdir()), "the build directory is removed")
        bundle = self.base("staging") / "releases" / A / "bundle"
        self.assertTrue((bundle / "deploy/hooks/backup").is_file())
        self.assertFalse((bundle / "README.md").exists(), "only the deploy bundle is kept")
        self.assertNotIn(SECRET_VALUE, output)

    def test_the_same_commit_again_changes_nothing(self) -> None:
        earlier = datetime.now(UTC) - timedelta(seconds=5)
        self.serve("release", self.request("publish", "staging", A, archive=source_archive(A), created=earlier))
        code, output = self.publish(A)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.last("staging")["result"], "unchanged")
        self.assertEqual(self.builder.built, [A], "a kept release is not built again")

    def test_promotion_reuses_the_staging_build_and_names_the_version(self) -> None:
        self.publish(A)
        code, output = self.serve("release", self.request("promote", "production", A, version="v1.0.0"))
        self.assertEqual(code, 0, output)
        self.assertIn("reusing the images built for staging", output)
        self.assertEqual(self.builder.built, [A])
        production = self.last("production")
        self.assertEqual((production["result"], production["version"], production["commit"]), ("deployed", "v1.0.0", A))
        staging_images = json.loads((self.base("staging") / "releases" / A / "record.json").read_text())["images"]
        self.assertEqual(production["images"], staging_images)

    def test_promotion_needs_the_commit_on_staging_and_a_new_version(self) -> None:
        code, output = self.serve("release", self.request("promote", "production", A, version="v1.0.0",
                                                           archive=source_archive(A)))
        self.assertEqual(code, 2)
        self.assertIn("never deployed to staging here", output)
        self.publish(A)
        self.serve("release", self.request("promote", "production", A, version="v1.0.0"))
        self.publish(B)
        code, output = self.serve("release", self.request("promote", "production", B, version="v1.0.0"))
        self.assertEqual(code, 2)
        self.assertIn("v1.0.0 already named another commit", output)

    def test_rollback_reuses_a_kept_release_and_refuses_a_schema_change(self) -> None:
        self.publish(A)
        self.publish(B)
        code, output = self.serve("release", self.request("rollback", "staging", A))
        self.assertEqual(code, 0, output)
        deployment = self.last("staging")
        self.assertEqual((deployment["result"], deployment["commit"], deployment["trigger"]), ("deployed", A, "rollback"))
        record = json.loads((self.base("staging") / "releases" / A / "record.json").read_text())
        self.assertTrue(record["rollback"])
        self.assertEqual(self.builder.built, [A, B])

        self.publish(C, migration="0002")
        code, output = self.serve("release", self.request("rollback", "staging", B))
        self.assertEqual(code, 2)
        self.assertIn("crosses a schema change", output)
        code, output = self.serve("release", self.request("rollback", "staging", B, allow_schema=True))
        self.assertEqual(code, 0, output)

    def test_rollback_only_to_a_release_deployed_here(self) -> None:
        self.publish(A)
        code, output = self.serve("release", self.request("rollback", "staging", B, archive=source_archive(B)))
        self.assertEqual(code, 2)
        self.assertIn("never deployed to staging", output)
        code, output = self.serve("release", self.request("rollback", "staging", A))
        self.assertEqual(code, 2)
        self.assertIn("already runs", output)

    def test_history_lists_deployments_and_kept_releases(self) -> None:
        self.publish(A)
        self.publish(B)
        code, output = self.serve("history example")
        self.assertEqual(code, 0, output)
        history = json.loads(output)
        staging = history["environments"]["staging"]
        self.assertEqual([entry["commit"] for entry in staging["deployments"]], [A, B])
        self.assertEqual(staging["kept"], [A, B])
        self.assertIsNone(history["environments"]["production"]["current"])
        code, output = self.serve("status example")
        self.assertEqual(json.loads(output)["environments"]["staging"]["current"]["commit"], B)

    def test_a_release_from_the_registry_era_counts_as_running(self) -> None:
        base = self.base("production")
        release = base / "releases" / A
        release.mkdir(parents=True)
        (release / "record.json").write_text(json.dumps({"schema": "af.release/v1", "commit": A, "schema_version": "0001"}))
        (base / "current").symlink_to(f"releases/{A}")
        (base / "deployments.jsonl").write_text(json.dumps({"commit": A, "result": "deployed", "schema_version": "0001"}) + "\n")
        code, output = self.serve("status example")
        self.assertEqual(json.loads(output)["environments"]["production"]["running"], A)
        code, output = self.serve("release", self.request("rollback", "production", A))
        self.assertEqual(code, 2)
        self.assertIn("already runs in production", output, "the rollback check sees it too")

    def test_a_missing_image_fails_before_anything_stops(self) -> None:
        self.publish(A)
        record = json.loads((self.base("staging") / "releases" / A / "record.json").read_text())
        self.docker.missing.add(record["images"]["web"])
        code, output = self.serve("release", self.request("promote", "production", A, version="v1.0.0"))
        self.assertEqual(code, 1)
        self.assertIn("image web of this release is no longer on this host", output)

    def test_retention_untags_images_no_environment_keeps(self) -> None:
        commits = [f"{n:x}" * 40 for n in range(1, 6)]
        for commit in commits:
            self.publish(commit)
            os.utime(self.base("staging") / "releases" / commit)
        self.assertEqual(len(list((self.base("staging") / "releases").iterdir())), 3)
        removed = [image for step, image in self.docker.calls if step == "image-rm"]
        self.assertIn(af_receive.image_tag("example", "web", commits[0]), removed)


class RobustnessTests(DirectHost):
    def test_a_signed_request_runs_once(self) -> None:
        signed = self.request("publish", "staging", A, archive=source_archive(A))
        code, output = self.serve("release", signed)
        self.assertEqual(code, 0, output)
        code, output = self.serve("release", signed)
        self.assertEqual(code, 2)
        self.assertIn("this signed request already ran here", output)

    def test_a_request_from_the_future_is_refused(self) -> None:
        ahead = datetime.now(UTC) + timedelta(minutes=5)
        code, output = self.serve("release", self.request("publish", "staging", A, archive=source_archive(A), created=ahead))
        self.assertEqual(code, 2)
        self.assertIn("not fresh", output)
        slightly = datetime.now(UTC) + timedelta(seconds=60)
        code, output = self.serve("release", self.request("publish", "staging", A, archive=source_archive(A),
                                                          created=slightly))
        self.assertEqual(code, 0, output)

    def test_a_failed_build_is_reported_to_the_owner(self) -> None:
        self.builder.fails = True
        code, output = self.serve("release", self.request("publish", "staging", A, archive=source_archive(A)))
        self.assertEqual(code, 1)
        self.assertIn("building web failed", output)
        self.assertEqual(self.notifier.sent, [("failed", f"example staging: failed unversioned ({A[:12]}) via direct "
                                                         "by device:laptop: building web failed")])

    def test_the_environment_lock_covers_the_build(self) -> None:
        base = self.base("staging")
        held = af_engine.acquire_lock(base, 0)
        try:
            code, output = self.serve("release", self.request("publish", "staging", A, archive=source_archive(A)))
        finally:
            os.close(held)
        self.assertEqual(code, 75, output)
        self.assertIn("another deployment holds the lock", output)
        self.assertEqual(self.builder.built, [], "nothing is built while another release holds the environment")

    def test_a_build_that_times_out_is_a_failure_not_a_traceback(self) -> None:
        def slow(*arguments, **options):
            raise subprocess.TimeoutExpired(arguments[0], options["timeout"])
        builder = af_receive.Builder(self.root / "build.lock", run=slow)
        with self.assertRaisesRegex(af_receive.ReceiveError, "took longer than 60 s") as caught:
            builder._ensure_builder(af_limits.Settings(), SMALL_HOST)
        self.assertEqual(caught.exception.exit_code, af_engine.EXIT_FAILED)

    def test_revoking_what_is_not_there_says_so(self) -> None:
        cases = ((argparse.Namespace(device="ghost", project=None), "has no key here"),
                 (argparse.Namespace(device=DEVICE, project="other"), "may not release other here anyway"))
        for args, message in cases:
            with self.subTest(message), self.assertRaisesRegex(af_receive.ReceiveError, message) as caught:
                af_receive.revoke(args, self.files)
            self.assertEqual(caught.exception.exit_code, af_receive.EXIT_NOTHING_MATCHED)
        self.assertIn("example", self.files.signers.read_text(), "nothing changed")

    def test_the_registry_era_release_is_republished_and_its_images_go(self) -> None:
        base = self.base("staging")
        release = base / "releases" / A
        bundle = release / "bundle"
        with tarfile.open(fileobj=io.BytesIO(source_archive(A))) as archive:
            for member in archive.getmembers():
                if member.name.startswith("deploy/") or member.name == "compose.yaml":
                    archive.extract(member, bundle, filter="data")
        pulled = "ghcr.io/owner/example@sha256:" + "e" * 64
        record = {"schema": "af.release/v1", "project": "example", "commit": A, "version": None, "created": "2026-10-01T00:00:00Z",
                  "images": {"web": pulled}, "schema_version": "0001", "rollback": False}
        (release / "record.json").write_text(json.dumps(record))
        (base / "current").symlink_to(f"releases/{A}")
        code, output = self.publish(A)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.last("staging")["result"], "deployed")
        self.assertIn(("image-rm", pulled), self.docker.calls, "the pulled image does not linger")

class BuilderTests(unittest.TestCase):
    def test_a_build_path_that_resolves_outside_the_tree_is_refused(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "source"
        (source / "docker").mkdir(parents=True)
        (root / "secrets").mkdir()
        (source / "secrets").symlink_to("../secrets")
        calls = []
        builder = af_receive.Builder(root / "build.lock", run=lambda argv, **options: calls.append(argv))
        for image in ({"name": "web", "context": "secrets"}, {"name": "web", "dockerfile": "secrets/token"}):
            with self.subTest(image=image), self.assertRaisesRegex(af_receive.ReceiveError, "outside the source tree"):
                builder._build_one(source, image, "example", A)
        self.assertEqual(calls, [])


class DiskDocker:
    """`docker` for the disk preflight and the builder: its data root, usage totals, the build
    container's cache (`cache` bytes, growing by `growth` per build), and builds."""

    def __init__(self, info_fails: bool = False, build_fails: bool = False, driver_options: str | None = None,
                 refused_flags: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.info_fails = info_fails
        self.build_fails = build_fails
        self.driver_options = driver_options
        self.refused_flags = refused_flags
        self.cache = 2 * af_receive.GB
        self.growth = 3 * af_receive.GB

    def __call__(self, argv: list[str], **options) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        arguments = argv[1:]
        stdout = ""
        code = 0
        if arguments[0] == "info":
            code, stdout = (1, "") if self.info_fails else (0, "/var/lib/docker\n")
        elif arguments[:2] == ["system", "df"]:
            stdout = "Images 31GB (16GB reclaimable)\nBuild Cache 8GB (8GB reclaimable)\n"
        elif arguments[:2] == ["image", "inspect"]:
            stdout = "1000000000\n" if "{{.Size}}" in arguments else "sha256:" + "a" * 64 + "\n"
        elif arguments[:2] == ["buildx", "du"]:
            stdout = f"ID  RECLAIMABLE  SIZE\nReclaimable:\t{self.cache}B\nTotal:\t\t{self.cache}B\n"
        elif arguments[:2] == ["buildx", "inspect"] and self.driver_options is not None:
            stdout = f"Name: pakem-build\nDriver Options:        {self.driver_options}\nStatus: running\n"
        elif arguments[:2] == ["buildx", "build"]:
            code = 1 if self.build_fails else 0
            self.cache += self.growth
        elif arguments[:2] == ["buildx", "prune"] and set(arguments) & set(self.refused_flags):
            code = 1
        return subprocess.CompletedProcess(argv, code, stdout, "")

    def builds(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[1] == "buildx" and argv[2] not in ("du", "prune", "inspect")]

    def prunes(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[1:3] == ["buildx", "prune"]]


def free_space(total: float = 100, **gigabytes: float):
    """A disk_usage whose free space depends on the path: root= for Docker, base= for the base."""
    def usage(path: Path):
        key = "root" if str(path).startswith("/var/lib/docker") else "base"
        return SimpleNamespace(free=int(gigabytes[key] * af_receive.GB), total=int(total * af_receive.GB))
    return usage


SMALL_HOST = af_limits.Host(cpus=4, memory=16 * 1000**3)


class DiskPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = {"images": [{"name": "web"}]}

    def builder(self, docker: DiskDocker, settings: af_limits.Settings | None = None, **gigabytes: float):
        return af_receive.Builder(self.root / "build.lock", run=docker, disk_usage=free_space(**gigabytes),
                                  settings=settings or af_limits.Settings(), host=SMALL_HOST, state_dir=self.root)

    def test_too_little_space_refuses_before_any_build_command(self) -> None:
        docker = DiskDocker()
        builder = self.builder(docker, root=4.2, base=50)
        with self.assertRaises(af_receive.ReceiveError) as caught:
            builder.build(self.root, self.config, "example", A, min_free_gb=10, base=self.root / "base")
        self.assertEqual(caught.exception.exit_code, af_engine.EXIT_REFUSED)
        message = str(caught.exception)
        self.assertIn("4.2GB free on the Docker data root, which keeps at least 10.0GB free "
                      "(min_free_gb in this environment's host config)", message)
        self.assertIn("Docker uses Images 31GB (16GB reclaimable), Build Cache 8GB (8GB reclaimable)", message)
        self.assertIn("Nothing was built or deleted", message)
        self.assertIn("lower `keep`", message)
        self.assertEqual(docker.builds(), [], "the preflight runs before BuildKit is touched")

    def test_a_full_release_base_is_refused_too(self) -> None:
        docker = DiskDocker()
        with self.assertRaisesRegex(af_receive.ReceiveError, "2.0GB free on the release base"):
            self.builder(docker, root=80, base=2).build(
                self.root, self.config, "example", A, min_free_gb=10, base=self.root / "base")
        self.assertEqual(docker.builds(), [])

    def test_enough_space_builds(self) -> None:
        docker = DiskDocker()
        source = self.root / "source"
        source.mkdir()
        images = self.builder(docker, root=40, base=40).build(
            source, self.config, "example", A, min_free_gb=10, base=self.root / "base")
        self.assertEqual(images, {"web": "sha256:" + "a" * 64})
        self.assertTrue(any(argv[1:3] == ["buildx", "build"] for argv in docker.calls))

    def test_an_unreadable_docker_root_is_a_failure_not_a_silent_pass(self) -> None:
        docker = DiskDocker(info_fails=True)
        with self.assertRaisesRegex(af_receive.ReceiveError, "could not read Docker's data root") as caught:
            self.builder(docker, root=40, base=40).build(self.root, self.config, "example", A, min_free_gb=10)
        self.assertEqual(caught.exception.exit_code, af_engine.EXIT_FAILED)
        self.assertEqual(docker.builds(), [])

    def test_zero_turns_the_check_off(self) -> None:
        docker = DiskDocker()
        self.builder(docker, root=0, base=0).check_space(0, self.root)
        self.assertEqual(docker.calls, [])

    def test_a_root_the_deploy_user_cannot_enter_is_measured_at_its_parent(self) -> None:
        seen = []

        def usage(path: Path):
            seen.append(str(path))
            if str(path) == "/var/lib/docker":
                raise PermissionError(13, "Permission denied")
            return SimpleNamespace(free=40 * af_receive.GB, total=100 * af_receive.GB)

        builder = af_receive.Builder(self.root / "build.lock", run=DiskDocker(), disk_usage=usage,
                                     settings=af_limits.Settings(), host=SMALL_HOST, state_dir=self.root)
        builder.check_space(10)
        self.assertEqual(seen, ["/var/lib/docker", "/var/lib"])


class RefusalTests(DirectHost):
    def assert_refused(self, stdin: bytes, message: str, command: str = "release", **options) -> None:
        code, output = self.serve(command, stdin, **options)
        self.assertEqual(code, 2, output)
        self.assertIn(message, output)
        self.assertFalse((self.base("staging") / "deployment.json").exists())
        self.assertEqual(self.builder.built, [])

    def test_only_this_device_key_signs(self) -> None:
        other = self.make_key("other-key")
        self.assert_refused(self.request("publish", "staging", A, archive=source_archive(A), key=other),
                            "not signed by device laptop's key")

    def test_another_device_or_project_is_refused(self) -> None:
        self.assert_refused(self.request("publish", "staging", A, archive=source_archive(A), device="desktop"),
                            "names another device")
        self.assert_refused(self.request("publish", "staging", A, archive=source_archive(A), project="other"),
                            "may not release other")

    def test_production_takes_only_a_promotion(self) -> None:
        self.assert_refused(self.request("publish", "production", A, archive=source_archive(A)),
                            "production takes a promotion, never a publish")
        self.assertFalse((self.base("production") / "deployment.json").exists())
        self.assert_refused(self.request("promote", "staging", A, version="v1.0.0", archive=source_archive(A)),
                            "a promotion goes only to production")

    def test_a_stale_request_is_refused(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=1)
        self.assert_refused(self.request("publish", "staging", A, archive=source_archive(A), created=old), "not fresh")

    def test_the_archive_must_match_the_signature(self) -> None:
        signed = self.request("publish", "staging", A, archive=source_archive(A))
        request, signature, _ = signed.split(b"\n", 2)
        self.assert_refused(request + b"\n" + signature + b"\n" + source_archive(B), "does not match the signed request")

    def test_a_tampered_request_is_refused(self) -> None:
        signed = self.request("publish", "staging", A, archive=source_archive(A))
        self.assert_refused(signed.replace(b'"allow_schema":false', b'"allow_schema":true', 1), "not signed by device laptop's key")

    def test_the_source_keeps_git_modes_for_the_images_users(self) -> None:
        archive = self.root / "source.tar"
        archive.write_bytes(source_archive(A))
        destination = self.root / "tree"
        af_receive.extract_source(archive, destination)
        self.assertEqual(oct((destination / "deploy" / "release.toml").stat().st_mode & 0o777), "0o644")
        self.assertEqual(oct((destination / "deploy" / "hooks" / "backup").stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((destination / "deploy").stat().st_mode & 0o777), "0o755")

    def test_unsafe_archives_are_refused(self) -> None:
        for name, member in (("traversal", "../escape"), ("absolute", "/etc/escape")):
            with self.subTest(name):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    info = tarfile.TarInfo(member)
                    archive.addfile(info, io.BytesIO(b""))
                self.assert_refused(self.request("publish", "staging", A, archive=buffer.getvalue()), "escapes")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
            archive.addfile(info)
        self.assert_refused(self.request("publish", "staging", A, archive=buffer.getvalue()), "points outside")

    def extract(self, *entries: tuple[str, str, str]) -> Path:
        """Extract (kind, name, link target or content) entries; returns the tree."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for kind, name, value in entries:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, value
                    archive.addfile(info)
                elif kind == "dir":
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                else:
                    info.size = len(value)
                    archive.addfile(info, io.BytesIO(value.encode()))
        work = Path(tempfile.mkdtemp(dir=self.root))
        (work / "source.tar").write_bytes(buffer.getvalue())
        af_receive.extract_source(work / "source.tar", work / "tree")
        return work / "tree"

    def assert_contained(self, message: str, *entries: tuple[str, str, str]) -> None:
        with self.assertRaisesRegex(af_receive.ReceiveError, message):
            self.extract(*entries)
        self.assertEqual(list(self.root.rglob("escaped*")), [])

    def test_chained_links_that_leave_the_tree_are_refused(self) -> None:
        self.assert_contained("points outside", ("link", "p/q", ".."), ("link", "r", "p/q/.."))

    def test_nothing_is_written_through_a_link(self) -> None:
        cases = {
            "a file through a link": (("link", "p/q", ".."), ("link", "r", "p/q/.."), ("file", "r/escaped.txt", "x")),
            "a file through an inside link": (("dir", "d", ""), ("link", "l", "d"), ("file", "l/escaped.txt", "x")),
            "a directory through a link": (("link", "p/q", ".."), ("link", "r", "p/q/.."), ("dir", "r/escaped", "")),
            "a link through a link": (("link", "p/q", ".."), ("link", "r", "p/q/.."), ("link", "r/escaped", "x")),
        }
        for name, entries in cases.items():
            with self.subTest(name):
                self.assert_contained("passes through a symlink", *entries)

    def test_an_entry_given_twice_is_refused(self) -> None:
        self.assert_contained("entry twice", ("file", "a", "x"), ("link", "a", "b"))

    def test_links_that_stay_inside_are_kept(self) -> None:
        tree = self.extract(("file", "docs/guide.md", "guide"), ("link", "README.md", "docs/guide.md"),
                            ("link", "docs/self", "."), ("link", "dangling", "missing"))
        self.assertEqual((tree / "README.md").read_text(), "guide")
        self.assertEqual(os.readlink(tree / "dangling"), "missing")

    def test_commands_outside_the_protocol_are_refused(self) -> None:
        for command in ("", "sh", "history", "history other", "deploy staging"):
            with self.subTest(command=command):
                code, _ = self.serve(command)
                self.assertEqual(code, 2)

    def test_a_registry_environment_takes_no_direct_release(self) -> None:
        (self.config_dir / "example-staging.toml").write_text(
            'project = "example"\nenvironment = "staging"\nbase = "/srv/x"\n'
            'pointer = "ghcr.io/o/example:release-staging"\npublic_key = "/etc/af/keys/example.pub"\n'
        )
        self.assert_refused(self.request("publish", "staging", A, archive=source_archive(A)), "no direct staging")


class AuthorizeTests(DirectHost):
    def test_writes_direct_configs_bases_and_both_key_lines(self) -> None:
        host = af_engine.HostConfig.load(self.config_dir / "example-production.toml")
        self.assertEqual((host.delivery, host.base, host.pointer), ("direct", self.base("production"), None))
        self.assertEqual(oct(self.base("production").stat().st_mode & 0o777), "0o700")
        authorized = self.files.authorized_keys.read_text()
        self.assertIn('restrict,command="/usr/bin/python3 /opt/af/af_receive.py serve --device laptop --project example"',
                      authorized)
        self.assertTrue(authorized.strip().endswith("pakem-device laptop"))
        self.assertEqual(oct(self.files.authorized_keys.stat().st_mode & 0o777), "0o600")
        signers = self.files.signers.read_text()
        self.assertTrue(signers.startswith(f'example namespaces="pakem-release" {self.public(self.key)}'))

    def test_a_second_project_joins_the_same_lines_and_keeps_other_keys(self) -> None:
        self.files.authorized_keys.write_text("ssh-ed25519 AAAAother someone-else\n" + self.files.authorized_keys.read_text())
        output = self.authorize(DEVICE, self.key, "second", ["production"], placement=["PUBLIC_HOST"], secret=["TOKEN"])
        self.assertIn("missing placement settings: PUBLIC_HOST", output)
        self.assertIn("missing secret settings: TOKEN", output)
        authorized = self.files.authorized_keys.read_text().splitlines()
        self.assertEqual(authorized[0], "ssh-ed25519 AAAAother someone-else")
        self.assertIn("--project example --project second", authorized[1])
        self.assertIn("example,second namespaces=", self.files.signers.read_text())
        self.authorize(DEVICE, self.key, "second", ["production"])
        self.assertEqual(self.files.authorized_keys.read_text().count("--project second"), 1, "re-runs change nothing")

    def test_the_signers_file_decides_which_devices_exist(self) -> None:
        forged = self.public(self.make_key("forged"))
        self.files.authorized_keys.write_text(
            self.files.authorized_keys.read_text()
            + f'restrict,command="/bin/sh" {forged} pakem-device intruder\n'
        )
        self.authorize(DEVICE, self.key, "second", ["production"])
        authorized = self.files.authorized_keys.read_text()
        self.assertNotIn("intruder", authorized, "a device line that is not in allowed_signers is dropped")
        self.assertNotIn("intruder", self.files.signers.read_text())
        with self.assertRaisesRegex(af_receive.ReceiveError, "intruder has no key here"):
            af_receive.revoke(argparse.Namespace(device="intruder", project=None), self.files)

    def test_a_key_belongs_to_one_device(self) -> None:
        with self.assertRaisesRegex(af_receive.ReceiveError, "already authorized as device laptop"):
            self.authorize("desktop", self.key, "example", ["staging"])
        with self.assertRaisesRegex(af_receive.ReceiveError, "has another key here"):
            self.authorize(DEVICE, self.make_key("new-key"), "example", ["staging"])

    def test_a_registry_config_switches_only_when_asked(self) -> None:
        base = self.deploy_root / "legacy" / "production"
        base.mkdir(parents=True, mode=0o700)
        path = self.config_dir / "legacy-production.toml"
        path.write_text(
            f'project = "legacy"\nenvironment = "production"\nbase = "{base}"\n'
            'pointer = "ghcr.io/o/legacy:release-production"\npublic_key = "/etc/af/keys/legacy.pub"\n'
            'notify = "/etc/af/notify.toml"\n\n[proxy]\nsites = "/etc/caddy/sites"\nreload = ["caddy", "reload"]\n'
        )
        with self.assertRaisesRegex(af_receive.ReceiveError, "deploys from a registry"):
            self.authorize(DEVICE, self.key, "legacy", ["production"])
        self.assertIn("pointer", path.read_text())
        output = self.authorize(DEVICE, self.key, "legacy", ["production"], switch_from_registry=True)
        self.assertIn("switched to direct delivery", output)
        host = af_engine.HostConfig.load(path)
        self.assertEqual((host.delivery, host.pointer, str(host.notify)), ("direct", None, "/etc/af/notify.toml"))
        self.assertEqual(host.proxy.reload, ["caddy", "reload"])

    def test_revoke_removes_a_project_then_the_device(self) -> None:
        self.authorize(DEVICE, self.key, "second", ["production"])
        args = argparse.Namespace(device=DEVICE, project="example")
        with redirect_stdout(io.StringIO()):
            af_receive.revoke(args, self.files)
        self.assertNotIn("--project example", self.files.authorized_keys.read_text())
        self.assertIn("--project second", self.files.authorized_keys.read_text())
        with redirect_stdout(io.StringIO()):
            af_receive.revoke(argparse.Namespace(device=DEVICE, project=None), self.files)
        self.assertNotIn("pakem-device", self.files.authorized_keys.read_text())
        self.assertNotIn("pakem-device", self.files.signers.read_text())
        code, output = self.serve("release", self.request("publish", "staging", A, archive=source_archive(A)))
        self.assertEqual(code, 2)
        self.assertIn("may not sign releases on this host", output)


class OtherHostServicesTests(DirectHost):
    def test_the_pull_agent_skips_and_the_push_trigger_refuses_direct_environments(self) -> None:
        polled = []
        agent = af_trigger.Agent(engine=lambda host, **options: polled.append(host))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(agent.poll(self.config_dir), 0)
            code = af_trigger.receive(self.config_dir, "example", [], "deploy staging")
        self.assertEqual(polled, [])
        self.assertEqual(code, 2)

    def test_the_engine_command_refuses_and_the_monitor_checks_direct_environments(self) -> None:
        host = af_engine.HostConfig.load(self.config_dir / "example-staging.toml")
        with self.assertRaisesRegex(af_engine.EngineError, "arrive through af_receive.py"):
            with redirect_stdout(io.StringIO()):
                af_engine.Engine(host).deploy()
        self.publish(A)
        monitor = af_monitor.Monitor(engine=lambda host: self.engine(host))
        with redirect_stdout(io.StringIO()):
            self.assertTrue(monitor.check(host))


if __name__ == "__main__":
    unittest.main()
