"""Release engine sequence: order of steps, restore, schema rule, records (fake Docker)."""

from __future__ import annotations

import fcntl
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from registry_fake import FakeRegistry
from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_notify  # noqa: E402
import af_oci  # noqa: E402
import af_release  # noqa: E402
import af_settings  # noqa: E402
import contracts  # noqa: E402

EXAMPLES = DEPLOY_ASSETS / "contracts"
REPO = "owner/example"
SECRET_VALUE = "db-password-never-printed"
PLACEMENT = "PUBLIC_HOST=example.test\nHTTP_PORT=18081\nDATA_DIR=/srv/data/example\n"


def bundle(*, backup_hook: bool = True, compose: bytes = b"services: {}\n") -> bytes:
    files = {
        "compose.yaml": compose,
        "deploy/release.toml": (EXAMPLES / "release.toml").read_bytes(),
        "deploy/settings.toml": (EXAMPLES / "settings.toml").read_bytes(),
        "deploy/env/production.env": (EXAMPLES / "production.env").read_bytes(),
    }
    if backup_hook:
        files["deploy/hooks/backup"] = b"#!/bin/sh\nexit 0\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if "/hooks/" in name else 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeDocker:
    """Answers the engine's docker commands and tracks which commit is running."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.running: str | None = None
        self.fail: set[tuple[str, str]] = set()  # (step, commit)
        self.unhealthy: set[str] = set()

    def __call__(self, arguments: list[str], env: dict, cwd: Path | None, timeout: int) -> int:
        commit = env.get("AF_COMMIT", "")
        if arguments[0].endswith("/hooks/backup"):
            step = "backup"
            if (step, commit) not in self.fail:
                Path(env["AF_BACKUP_DIR"], "database.dump").write_text("dump")
        elif arguments[:2] == ["docker", "pull"]:
            step = "pull"
        elif arguments[:3] == ["docker", "image", "rm"]:
            self.calls.append(("image-rm", arguments[3]))
            return 0
        else:
            sub = arguments[arguments.index("--file") + 2 :]
            step = sub[0]
            if step == "run":
                step = "migrate" if "app.migrate" in sub else "preflight"
        self.calls.append((step, commit[:4]))
        if (step, commit) in self.fail:
            return 1
        if step == "up":
            self.running = commit
        elif step == "stop":
            self.running = None
        return 0

    def health(self, url: str) -> dict | None:
        assert url == "http://127.0.0.1:18081/health/release"
        if self.running is None or self.running in self.unhealthy:
            return None
        return {"revision": self.running}

    def steps(self) -> list[str]:
        return [step for step, _ in self.calls]


class RecordingNotifier(af_notify.Notifier):
    def __init__(self) -> None:
        super().__init__([])
        self.sent: list[tuple[str, str]] = []

    def send(self, event: str, text: str, record: dict | None = None) -> None:
        self.sent.append((event, text))


class Host:
    def __init__(self, case: unittest.TestCase, registry: FakeRegistry) -> None:
        root = Path(case.enterContext(tempfile.TemporaryDirectory()))
        self.registry = registry
        self.base = root / "base"
        self.base.mkdir(mode=0o700)
        (self.base / "secrets").mkdir(mode=0o700)
        placement = self.base / "placement.env"
        placement.write_text(PLACEMENT)
        placement.chmod(0o600)
        af_settings.set_secret(self.base / "secrets", "DATABASE_PASSWORD", SECRET_VALUE.encode())
        self.config = af_engine.HostConfig(
            project="example",
            environment="production",
            base=self.base,
            pointer=f"{registry.host}/{REPO}:release-production",
            public_key=root / "unused.pub",
        )
        self.docker = FakeDocker()
        self.notifier = RecordingNotifier()
        self.bundles: dict[bytes, str] = {}

    def publish(self, commit: str, *, created: str, schema: str = "0009", rollback: bool = False, **bundle_options) -> dict:
        content = bundle(**bundle_options)
        bundle_digest = self.registry.push_artifact(REPO, None, af_release.BUNDLE_MEDIA, content)
        images = {
            name: f"{self.registry.host}/{REPO}@"
            + self.registry.push_artifact(REPO, None, "application/x-test-image", f"{name}-{commit}".encode())
            for name in ("web", "worker")
        }
        record = json.loads((EXAMPLES / "release-record.production.json").read_text())
        record.update(
            commit=commit, created=created, schema_version=schema, rollback=rollback, images=images,
            bundle=f"{self.registry.host}/{REPO}@{bundle_digest}",
        )
        self.registry.push_artifact(REPO, "release-production", af_release.RELEASE_MEDIA, json.dumps(record).encode())
        return record

    def deploy(self) -> dict:
        engine = af_engine.Engine(
            self.config,
            registry=af_oci.Registry(),
            verify=lambda reference: None,
            runner=self.docker,
            health=self.docker.health,
            sleep=lambda seconds: None,
            health_attempts=2,
            notifier=self.notifier,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            record = engine.deploy()
        self.output = output.getvalue()
        return record

    def current_commit(self) -> str | None:
        link = self.base / "current"
        return os.readlink(link).split("/")[1] if link.is_symlink() else None


A, B, C = "a" * 40, "b" * 40, "c" * 40


class Sequence(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = self.enterContext(FakeRegistry())
        self.host = Host(self, self.registry)

    def test_first_deployment_runs_every_step_except_stop_and_backup(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("deployed", 0))
        self.assertEqual(self.host.docker.steps(), ["pull", "pull", "config", "preflight", "migrate", "up"])
        self.assertEqual(self.host.current_commit(), A)
        self.assertIsNone(record["previous_commit"])
        self.assertEqual(contracts.validate_deployment_record(record), [])

    def test_retention_removes_old_releases_and_their_images(self) -> None:
        commits = [f"{n:x}" * 40 for n in range(1, 9)]
        published = {}
        for day, commit in enumerate(commits, start=10):
            published[commit] = self.host.publish(commit, created=f"2026-10-{day}T00:00:00Z")
            self.host.deploy()
            os.utime(self.host.base / "releases" / commit, (day * 1000, day * 1000))
        kept = sorted(path.name for path in (self.host.base / "releases").iterdir())
        self.assertEqual(kept, sorted(commits[2:]))  # production keeps 6
        removed = [image for step, image in self.host.docker.calls if step == "image-rm"]
        expected = [image for commit in commits[:2] for image in published[commit]["images"].values()]
        self.assertEqual(sorted(removed), sorted(expected))

    def test_the_same_release_again_changes_nothing(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.docker.calls.clear()
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("unchanged", 0))
        self.assertEqual(self.host.docker.calls, [])

    def test_upgrade_drains_backs_up_migrates_then_starts(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.docker.calls.clear()
        self.host.publish(B, created="2026-10-21T00:00:00Z", schema="0010")
        record = self.host.deploy()
        steps = self.host.docker.steps()
        self.assertEqual(steps, ["pull", "pull", "config", "preflight", "stop", "backup", "migrate", "up"])
        self.assertEqual(self.host.docker.calls[4], ("stop", "aaaa"))
        self.assertEqual(record["result"], "deployed")
        self.assertTrue(Path(record["backup"], "database.dump").exists())
        self.assertEqual((self.host.current_commit(), self.host.docker.running), (B, B))
        lines = (self.host.base / "deployments.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["result"] for line in lines], ["deployed", "deployed"])

    def test_unhealthy_release_with_the_same_schema_restores_the_previous(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.docker.unhealthy.add(B)
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("restored", 3))
        self.assertEqual((self.host.current_commit(), self.host.docker.running), (A, A))

    def test_every_recorded_result_is_notified_with_its_reason(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.docker.unhealthy.add(B)
        self.host.deploy()
        events = [event for event, _ in self.host.notifier.sent]
        self.assertEqual(events, ["deployed", "unchanged", "restored"])
        self.assertTrue(self.host.notifier.sent[2][1].endswith(", exit 3: health check did not report this release"))
        self.assertFalse(any(SECRET_VALUE in text for _, text in self.host.notifier.sent))

    def test_failure_after_a_schema_change_stops_for_the_owner(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z", schema="0010")
        self.host.docker.unhealthy.add(B)
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("stopped", 5))
        self.assertIsNone(self.host.docker.running)
        self.assertEqual(self.host.current_commit(), A)
        self.assertIn("restore from the backup", self.host.output)

    def test_failures_before_the_switch_leave_the_running_release_alone(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.docker.fail.add(("preflight", B))
        self.host.docker.calls.clear()
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("failed", 1))
        self.assertNotIn("stop", self.host.docker.steps())
        self.assertEqual(self.host.docker.running, A)

    def test_failed_backup_restores_without_migrating(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.docker.fail.add(("backup", B))
        self.host.docker.calls.clear()
        record = self.host.deploy()
        self.assertEqual(record["result"], "restored")
        self.assertNotIn("migrate", self.host.docker.steps())

    def test_production_migrations_without_a_backup_hook_are_not_applied(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z", backup_hook=False)
        self.host.docker.calls.clear()
        record = self.host.deploy()
        self.assertEqual(record["result"], "restored")
        self.assertNotIn("migrate", self.host.docker.steps())

    def test_older_releases_need_the_rollback_flag(self) -> None:
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.deploy()
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        with self.assertRaises(af_engine.EngineError) as raised:
            self.host.deploy()
        self.assertEqual(raised.exception.exit_code, af_engine.EXIT_REFUSED)
        self.host.publish(A, created="2026-10-20T00:00:00Z", rollback=True)
        self.assertEqual(self.host.deploy()["result"], "deployed")
        self.assertEqual(self.host.current_commit(), A)

    def test_a_republished_running_commit_must_keep_its_bundle(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(A, created="2026-10-22T00:00:00Z", compose=b"services: {changed: {}}\n")
        with self.assertRaisesRegex(af_engine.EngineError, "different bundle"):
            self.host.deploy()
        self.assertTrue((self.host.base / "releases" / A / "bundle/compose.yaml").exists())

    def test_a_held_lock_refuses_with_busy(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        descriptor = os.open(self.host.base / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(af_engine.EngineError) as raised:
                self.host.deploy()
            self.assertEqual(raised.exception.exit_code, af_engine.EXIT_BUSY)
        finally:
            os.close(descriptor)

    def test_output_and_records_never_contain_secret_values(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.deploy()
        session_key = (self.host.base / "secrets/SESSION_KEY").read_text()
        for text in (self.host.output, (self.host.base / "deployments.jsonl").read_text()):
            self.assertNotIn(SECRET_VALUE, text)
            self.assertNotIn(session_key, text)

    def test_unsafe_base_directory_is_refused(self) -> None:
        self.host.base.chmod(0o777)
        with self.assertRaises(af_engine.EngineError) as raised:
            self.host.deploy()
        self.assertEqual(raised.exception.exit_code, af_engine.EXIT_REFUSED)


class HostConfigFile(unittest.TestCase):
    def test_unknown_keys_and_relative_paths_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            valid = 'project = "example"\nenvironment = "production"\nbase = "/srv/x"\npointer = "ghcr.io/o/x:release-production"\npublic_key = "/etc/af/x.pub"\n'
            path.write_text(valid)
            self.assertEqual(af_engine.HostConfig.load(path).compose_project, "example-production")
            path.write_text(valid + 'extra = "x"\n')
            with self.assertRaises(af_engine.EngineError):
                af_engine.HostConfig.load(path)
            path.write_text(valid.replace("/srv/x", "srv/x"))
            with self.assertRaises(af_engine.EngineError):
                af_engine.HostConfig.load(path)


if __name__ == "__main__":
    unittest.main()
