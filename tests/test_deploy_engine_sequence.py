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


def bundle(
    *, backup_hook: bool = True, post_switch_hook: bool = False, compose: bytes = b"services: {}\n", switch: str = "stop-start"
) -> bytes:
    release_toml = (EXAMPLES / "release.toml").read_bytes().replace(b'switch = "stop-start"', f'switch = "{switch}"'.encode())
    files = {
        "compose.yaml": compose,
        "deploy/release.toml": release_toml,
        "deploy/settings.toml": (EXAMPLES / "settings.toml").read_bytes(),
        "deploy/env/production.env": (EXAMPLES / "production.env").read_bytes(),
    }
    if backup_hook:
        files["deploy/hooks/backup"] = b"#!/bin/sh\nexit 0\n"
    if post_switch_hook:
        files["deploy/hooks/post_switch"] = b"#!/bin/sh\nexit 0\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if "/hooks/" in name else 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeDocker:
    """Answers the engine's docker commands and models the web containers of the project.

    Each web container runs one commit on its own loopback port; `running` is the commit the
    project's web service serves (None when stopped, or while two commits run side by side).
    """

    STABLE_PORT = 18081

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.web: dict[str, tuple[str, int]] = {}  # container id -> (commit, port)
        self.fail: set[tuple[str, str]] = set()  # (step, commit)
        self.unhealthy: set[str] = set()
        self.counter = 0

    @property
    def running(self) -> str | None:
        commits = {commit for commit, _ in self.web.values()}
        return commits.pop() if len(commits) == 1 else None

    def start_web(self, commit: str, port: int) -> None:
        self.counter += 1
        self.web[f"web{self.counter:04d}"] = (commit, port)

    def __call__(self, arguments: list[str], env: dict, cwd: Path | None, timeout: int) -> int:
        commit = env.get("AF_COMMIT", "")
        if arguments[0] == "caddy":
            step = "reload"
        elif "/hooks/" in arguments[0]:
            step = arguments[0].rsplit("/", 1)[1]
            if step == "backup" and (step, commit) not in self.fail:
                Path(env["AF_BACKUP_DIR"], "database.dump").write_text("dump")
        elif arguments[:2] == ["docker", "pull"]:
            step = "pull"
        elif arguments[:3] == ["docker", "image", "rm"]:
            self.calls.append(("image-rm", arguments[3]))
            return 0
        elif arguments[:2] in (["docker", "stop"], ["docker", "rm"]):
            ids = [a for a in arguments[2:] if a.startswith("web")]
            self.calls.append((f"container-{arguments[1]}", ",".join(ids)))
            for container in ids:
                self.web.pop(container, None)
            return 0
        else:
            sub = arguments[arguments.index("--file") + 2 :]
            step = sub[0]
            if step == "run":
                step = "migrate" if "app.migrate" in sub else "preflight"
            elif step == "up" and "--scale" in sub:
                step = "beside"
        self.calls.append((step, commit[:4]))
        if (step, commit) in self.fail:
            return 1
        if step == "up":
            if not any(c == commit for c, _ in self.web.values()):
                self.web.clear()
                self.start_web(commit, self.STABLE_PORT)
        elif step == "beside":
            self.start_web(commit, self.STABLE_PORT + 1 + self.counter)
        elif step == "stop":
            self.web.clear()
        return 0

    def query(self, arguments: list[str], env: dict) -> tuple[int, str]:
        if arguments[:2] == ["docker", "ps"]:
            service = next(a.rsplit("=", 1)[1] for a in arguments if a.startswith("label=com.docker.compose.service="))
            return 0, "".join(f"{c}\n" for c in sorted(self.web)) if service == "web" else ""
        if arguments[:2] == ["docker", "port"] and arguments[2] in self.web:
            return 0, f"127.0.0.1:{self.web[arguments[2]][1]}\n"
        return 1, ""

    def health(self, url: str) -> dict | None:
        port = int(url.split(":")[2].split("/")[0])
        assert url.endswith("/health/release")
        for commit, container_port in self.web.values():
            if container_port == port and commit not in self.unhealthy:
                return {"revision": commit}
        return None

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
            query=self.docker.query,
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



class ProxyRoutes(unittest.TestCase):
    """Route files for the host proxy: written after the health check, reverted on restore."""

    def setUp(self) -> None:
        self.registry = self.enterContext(FakeRegistry())
        self.host = Host(self, self.registry)
        self.sites = self.host.base.parent / "sites"
        self.sites.mkdir()
        self.host.config.proxy = af_engine.ProxyConfig(self.sites, ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
        self.route_file = self.sites / "example-production.caddy"

    def set_placement(self, text: str) -> None:
        (self.host.base / "placement.env").write_text(text)

    def test_routes_follow_the_health_check_and_reload_only_on_change(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.assertEqual(self.host.deploy()["result"], "deployed")
        steps = self.host.docker.steps()
        self.assertEqual(steps[steps.index("up") + 1 :], ["reload"])
        self.assertIn("example.test {\n\treverse_proxy 127.0.0.1:18081\n}", self.route_file.read_text())
        self.assertEqual(oct(self.route_file.stat().st_mode & 0o777), "0o644")

        self.host.publish(B, created="2026-10-21T00:00:00Z")
        self.host.deploy()
        self.assertEqual(self.host.docker.steps().count("reload"), 1)

    def test_a_refused_reload_puts_the_old_routes_back_and_restores(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        before = self.route_file.read_text()
        self.set_placement(PLACEMENT.replace("PUBLIC_HOST=example.test", "PUBLIC_HOST=new.example.test"))
        self.host.docker.fail.add(("reload", ""))
        self.host.publish(B, created="2026-10-21T00:00:00Z")
        record = self.host.deploy()
        self.assertEqual((record["result"], record["exit_code"]), ("restored", 3))
        self.assertEqual(self.route_file.read_text(), before)
        self.assertEqual(self.host.current_commit(), A)

    def test_a_failure_after_the_switch_reverts_the_routes(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        before = self.route_file.read_text()
        self.set_placement(PLACEMENT.replace("PUBLIC_HOST=example.test", "PUBLIC_HOST=new.example.test"))
        self.host.publish(B, created="2026-10-21T00:00:00Z", post_switch_hook=True)
        self.host.docker.fail.add(("post_switch", B))
        record = self.host.deploy()
        self.assertEqual(record["result"], "restored")
        self.assertEqual(self.route_file.read_text(), before)
        self.assertEqual(self.host.docker.steps().count("reload"), 3)  # first, new, reverted

    def test_an_unhealthy_release_never_touches_the_routes(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.docker.unhealthy.add(A)
        self.host.deploy()
        self.assertFalse(self.route_file.exists())
        self.assertNotIn("reload", self.host.docker.steps())

    def test_placement_values_cannot_inject_proxy_configuration(self) -> None:
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        for host_value in ("example.test}", "Example.test", "example", "example.test:443", "*.example.test"):
            with self.subTest(value=host_value):
                self.set_placement(PLACEMENT.replace("PUBLIC_HOST=example.test", f"PUBLIC_HOST={host_value}"))
                self.host.publish(B, created="2026-10-21T00:00:00Z")
                with self.assertRaises(af_engine.EngineError) as caught:
                    self.host.deploy()
                self.assertEqual(caught.exception.exit_code, af_engine.EXIT_REFUSED)
                self.assertEqual(self.host.current_commit(), A)
        self.assertEqual(self.host.docker.steps().count("stop"), 0)

    def test_without_tls_the_site_is_plain_http_and_hosts_without_a_proxy_write_nothing(self) -> None:
        self.host.config.proxy = af_engine.ProxyConfig(self.sites, ["caddy", "reload"], tls=False)
        self.host.publish(A, created="2026-10-20T00:00:00Z")
        self.host.deploy()
        self.assertIn("http://example.test {", self.route_file.read_text())

        other = Host(self, self.registry)
        other.publish(B, created="2026-10-21T00:00:00Z")
        other.deploy()
        self.assertNotIn("reload", other.docker.steps())

    def test_proxy_settings_are_validated(self) -> None:
        for proxy in ({"sites": "relative"}, {"sites": "/s", "reload": []}, {"sites": "/s", "reload": ["c"], "tls": "no"},
                      {"sites": "/s", "reload": ["c"], "extra": 1}):
            with self.subTest(proxy=proxy), self.assertRaises(af_engine.EngineError):
                af_engine.ProxyConfig.parse(proxy)


class BlueGreen(unittest.TestCase):
    """The new release starts beside the old one; the old one serves until the routes move."""

    def setUp(self) -> None:
        self.registry = self.enterContext(FakeRegistry())
        self.host = Host(self, self.registry)
        self.sites = self.host.base.parent / "sites"
        self.sites.mkdir()
        self.host.config.proxy = af_engine.ProxyConfig(self.sites, ["caddy", "reload"])
        self.route_file = self.sites / "example-production.caddy"
        self.host.publish(A, created="2026-10-20T00:00:00Z", switch="blue-green")
        self.assertEqual(self.host.deploy()["result"], "deployed")  # the first release starts normally
        self.host.docker.calls.clear()

    def upgrade(self) -> dict:
        self.host.publish(B, created="2026-10-21T00:00:00Z", switch="blue-green")
        return self.host.deploy()

    def test_routes_move_to_the_new_container_before_the_old_one_stops(self) -> None:
        record = self.upgrade()
        self.assertEqual((record["result"], record["exit_code"]), ("deployed", 0))
        steps = self.host.docker.steps()
        self.assertNotIn("stop", steps)
        self.assertEqual(steps[steps.index("migrate"):], ["migrate", "beside", "reload", "container-stop", "container-rm", "up"])
        self.assertEqual(self.host.docker.running, B)
        new_port = next(port for commit, port in self.host.docker.web.values())
        self.assertNotEqual(new_port, FakeDocker.STABLE_PORT)
        self.assertIn(f"reverse_proxy 127.0.0.1:{new_port}\n", self.route_file.read_text())
        self.assertEqual(self.host.current_commit(), B)

    def test_an_unhealthy_new_container_is_removed_and_the_old_one_keeps_serving(self) -> None:
        routes = self.route_file.read_text()
        self.host.docker.unhealthy.add(B)
        record = self.upgrade()
        self.assertEqual((record["result"], record["exit_code"]), ("failed", 1))
        self.assertEqual(self.host.docker.running, A)
        self.assertEqual(self.route_file.read_text(), routes)
        self.assertNotIn("reload", self.host.docker.steps())
        self.assertEqual(self.host.current_commit(), A)

    def test_a_refused_route_change_keeps_the_old_container_serving(self) -> None:
        routes = self.route_file.read_text()
        self.host.docker.fail.add(("reload", ""))
        record = self.upgrade()
        self.assertEqual(record["result"], "failed")
        self.assertEqual(self.host.docker.running, A)
        self.assertEqual(self.route_file.read_text(), routes)

    def test_a_failed_migration_never_stops_the_running_release(self) -> None:
        self.host.docker.fail.add(("migrate", B))
        record = self.upgrade()
        self.assertEqual((record["result"], record["exit_code"]), ("failed", 1))
        self.assertEqual(self.host.docker.running, A)
        self.assertNotIn("stop", self.host.docker.steps())

    def test_blue_green_needs_a_proxy_on_the_host(self) -> None:
        self.host.config.proxy = None
        self.host.publish(B, created="2026-10-21T00:00:00Z", switch="blue-green")
        with self.assertRaises(af_engine.EngineError) as caught:
            self.host.deploy()
        self.assertEqual(caught.exception.exit_code, af_engine.EXIT_REFUSED)
        self.assertEqual(self.host.docker.running, A)

if __name__ == "__main__":
    unittest.main()
