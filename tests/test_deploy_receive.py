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

from support import DEPLOY_ASSETS
from test_deploy_engine_sequence import EXAMPLES, PLACEMENT, SECRET_VALUE, FakeDocker, RecordingNotifier

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
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

    def build(self, source: Path, config: dict, project: str, commit: str) -> dict[str, str]:
        self.built.append(commit)
        return {image["name"]: f"sha256:{hashlib.sha256(f'{image['name']}-{commit}'.encode()).hexdigest()}"
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
            builder=self.builder, engine=self.engine,
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
        self.publish(A)
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
