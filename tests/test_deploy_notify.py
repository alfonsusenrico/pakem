"""Notifications (af_notify.py) and the health monitor (af_monitor.py)."""

from __future__ import annotations

import fcntl
import io
import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_monitor  # noqa: E402
import af_notify  # noqa: E402

TOKEN = "123456:" + "A" * 35
HOOK = "https://hooks.example.test/services/T000/B000/" + "x" * 24
RECORD = {
    "project": "example", "environment": "production", "result": "restored", "exit_code": 3,
    "version": "v1.2.0", "commit": "c" * 40, "trigger": "push", "actor": "octocat",
}


class RecordingNotifier(af_notify.Notifier):
    def __init__(self) -> None:
        super().__init__([])
        self.sent: list[tuple[str, str]] = []

    def send(self, event: str, text: str, record: dict | None = None) -> None:
        self.sent.append((event, text))


class Files:
    def __init__(self, case: unittest.TestCase) -> None:
        self.root = Path(case.enterContext(tempfile.TemporaryDirectory()))

    def secret(self, name: str, value: str, mode: int = 0o600) -> Path:
        path = self.root / name
        path.write_text(value + "\n")
        path.chmod(mode)
        return path

    def channels(self, text: str) -> Path:
        path = self.root / "notify.toml"
        path.write_text(textwrap.dedent(text))
        return path


class Channels(unittest.TestCase):
    def setUp(self) -> None:
        self.files = Files(self)

    def test_a_valid_file_loads_with_default_events(self) -> None:
        path = self.files.channels(
            """\
            [[channels]]
            kind = "telegram"
            secret_file = "/etc/af/secrets/telegram"
            chat_id = "-100123"

            [[channels]]
            kind = "slack"
            secret_file = "/etc/af/secrets/slack"
            events = ["failed", "unhealthy"]
            """
        )
        telegram, slack = af_notify.load_channels(path)
        self.assertEqual(telegram.events, af_notify.DEFAULT_EVENTS)
        self.assertNotIn("unchanged", telegram.events)
        self.assertEqual(slack.events, {"failed", "unhealthy"})

    def test_invalid_channels_are_refused(self) -> None:
        cases = {
            "unknown kind": 'kind = "email"\nsecret_file = "/s"',
            "relative secret": 'kind = "slack"\nsecret_file = "s"',
            "telegram without chat": 'kind = "telegram"\nsecret_file = "/s"',
            "chat on slack": 'kind = "slack"\nsecret_file = "/s"\nchat_id = "1"',
            "unknown event": 'kind = "slack"\nsecret_file = "/s"\nevents = ["exploded"]',
            "unknown key": 'kind = "slack"\nsecret_file = "/s"\nurl = "https://x"',
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                path = self.files.channels("[[channels]]\n" + body + "\n")
                with self.assertRaises(af_notify.NotifyError):
                    af_notify.load_channels(path)

    def test_a_broken_file_disables_notifications_without_failing(self) -> None:
        lines: list[str] = []
        notifier = af_notify.from_file(self.files.channels("channels = 3\n"), lines.append)
        self.assertEqual(notifier.channels, [])
        self.assertTrue(lines[0].startswith("notifications disabled"))


class Delivery(unittest.TestCase):
    def setUp(self) -> None:
        self.files = Files(self)
        self.posts: list[tuple[str, dict]] = []
        self.lines: list[str] = []

    def notifier(self, *channels: af_notify.Channel, post=None) -> af_notify.Notifier:
        return af_notify.Notifier(list(channels), post=post or (lambda url, body: self.posts.append((url, body))),
                                  log=self.lines.append)

    def test_each_kind_posts_its_own_payload(self) -> None:
        telegram = af_notify.Channel("telegram", self.files.secret("t", TOKEN), "-100123")
        slack = af_notify.Channel("slack", self.files.secret("s", HOOK))
        discord = af_notify.Channel("discord", self.files.secret("d", HOOK))
        webhook = af_notify.Channel("webhook", self.files.secret("w", HOOK))
        self.notifier(telegram, slack, discord, webhook).send("restored", "text", RECORD)
        urls = [url for url, _ in self.posts]
        self.assertEqual(urls, [f"https://api.telegram.org/bot{TOKEN}/sendMessage", HOOK, HOOK, HOOK])
        bodies = [body for _, body in self.posts]
        self.assertEqual(bodies[0]["chat_id"], "-100123")
        self.assertEqual(bodies[1], {"text": "text"})
        self.assertEqual(bodies[2]["allowed_mentions"], {"parse": []})
        self.assertEqual(bodies[3]["deployment"], RECORD)

    def test_only_subscribed_events_are_sent(self) -> None:
        channel = af_notify.Channel("slack", self.files.secret("s", HOOK), events=frozenset({"failed"}))
        notifier = self.notifier(channel)
        notifier.send("deployed", "ok", RECORD)
        notifier.send("failed", "bad", RECORD)
        self.assertEqual([body["text"] for _, body in self.posts], ["bad"])

    def test_unsafe_secrets_are_not_used_and_never_logged(self) -> None:
        cases = [
            af_notify.Channel("slack", self.files.secret("open", HOOK, mode=0o644)),
            af_notify.Channel("slack", self.files.secret("http", "http://hooks.example.test/x")),
            af_notify.Channel("telegram", self.files.secret("bad", "not a token"), "1"),
        ]
        self.notifier(*cases).send("failed", "bad", RECORD)
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.lines), 3)
        self.assertFalse(any("hooks.example.test" in line or "not a token" in line for line in self.lines))

    def test_a_failing_channel_is_logged_and_the_others_still_get_the_message(self) -> None:
        def post(url: str, body: dict) -> None:
            if "telegram" in url:
                raise af_notify.NotifyError("HTTP 401")
            self.posts.append((url, body))

        telegram = af_notify.Channel("telegram", self.files.secret("t", TOKEN), "1")
        slack = af_notify.Channel("slack", self.files.secret("s", HOOK))
        self.notifier(telegram, slack, post=post).send("failed", "bad", RECORD)
        self.assertEqual(self.lines, ["notification via telegram failed: HTTP 401"])
        self.assertEqual(len(self.posts), 1)

    def test_http_errors_name_the_status_not_the_url(self) -> None:
        class Refuse(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Refuse)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/secret-path"
        with self.assertRaises(af_notify.NotifyError) as caught:
            af_notify.post_json(url, {"text": "x"})
        self.assertEqual(str(caught.exception), "HTTP 500")

    def test_deployment_text_names_identifiers_and_the_reason(self) -> None:
        text = af_notify.deployment_text(RECORD, "health check did not report this release")
        self.assertEqual(
            text,
            "example production: restored v1.2.0 (cccccccccccc) via push by octocat, exit 3: "
            "health check did not report this release",
        )


class FakeRelease:
    def __init__(self, commit: str) -> None:
        self.record = {"commit": commit}


class FakeEngine:
    def __init__(self, current: FakeRelease | None, healthy: list[bool]) -> None:
        self._current = current
        self.results = healthy

    def current(self) -> FakeRelease | None:
        return self._current

    def healthy(self, release: FakeRelease) -> bool:
        return self.results.pop(0)


class HealthMonitor(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.configs = root / "hosts"
        self.configs.mkdir()
        self.base = root / "base"
        self.base.mkdir(mode=0o700)
        (self.configs / "example-production.toml").write_text(
            f'project = "example"\nenvironment = "production"\nbase = "{self.base}"\n'
            'pointer = "ghcr.io/owner/example:release-production"\npublic_key = "/etc/af/keys/example.pub"\n'
        )
        self.notifier = RecordingNotifier()

    def run_checks(self, engine: FakeEngine, passes: int) -> list[int]:
        monitor = af_monitor.Monitor(engine=lambda host: engine, notifier=lambda host: self.notifier)
        results = []
        with redirect_stdout(io.StringIO()):
            for _ in range(passes):
                results.append(monitor.check_all(self.configs))
        return results

    def test_two_failures_alert_once_and_recovery_is_announced(self) -> None:
        engine = FakeEngine(FakeRelease("c" * 40), [False, False, False, True])
        self.assertEqual(self.run_checks(engine, 4), [1, 1, 1, 0])
        events = [event for event, _ in self.notifier.sent]
        self.assertEqual(events, ["unhealthy", "recovered"])
        self.assertIn("example production: health check failing for cccccccccccc", self.notifier.sent[0][1])
        self.assertFalse((self.base / "monitor.json").exists())

    def test_one_failure_then_success_stays_quiet(self) -> None:
        engine = FakeEngine(FakeRelease("c" * 40), [False, True])
        self.run_checks(engine, 2)
        self.assertEqual(self.notifier.sent, [])

    def test_nothing_deployed_or_a_deployment_running_is_not_checked(self) -> None:
        self.assertEqual(self.run_checks(FakeEngine(None, []), 1), [0])
        descriptor = os.open(self.base / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.assertEqual(self.run_checks(FakeEngine(FakeRelease("c" * 40), []), 1), [0])

    def test_an_unusable_config_reports_attention(self) -> None:
        (self.configs / "broken.toml").write_text("project = \n")
        engine = FakeEngine(FakeRelease("c" * 40), [True])
        self.assertEqual(self.run_checks(engine, 1), [1])


class EngineNotifies(unittest.TestCase):
    def test_host_config_accepts_an_absolute_notify_path_only(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        common = 'project = "example"\nenvironment = "staging"\nbase = "/srv/x"\npointer = "ghcr.io/o/x:release-staging"\npublic_key = "/k"\n'
        good, bad = root / "good.toml", root / "bad.toml"
        good.write_text(common + 'notify = "/etc/af/notify.toml"\n')
        bad.write_text(common + 'notify = "notify.toml"\n')
        self.assertEqual(af_engine.HostConfig.load(good).notify, Path("/etc/af/notify.toml"))
        with self.assertRaises(af_engine.EngineError):
            af_engine.HostConfig.load(bad)


if __name__ == "__main__":
    unittest.main()
