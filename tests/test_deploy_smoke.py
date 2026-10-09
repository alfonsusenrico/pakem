"""Unit tests for the deploy asset smoke.py against a loopback HTTP server."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from support import load_deploy_script

smoke = load_deploy_script("smoke.py")

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "f" * 40


class Handler(BaseHTTPRequestHandler):
    revision = SHA

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path == "/release.json":
            self.reply(200, json.dumps({"revision": type(self).revision, "secret_body": "do-not-print"}).encode())
        elif self.path == "/ok":
            self.reply(200, b"fine")
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/")
            self.end_headers()
        else:
            self.reply(404, b"missing")

    def reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: ANN002 - silence the test server
        return


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        Handler.revision = SHA
        self.sleeps: list[float] = []

    def run_smoke(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = smoke.main(list(args), sleep=self.sleeps.append)
        return code, output.getvalue()

    def test_matching_revision_and_paths_pass(self) -> None:
        code, output = self.run_smoke(
            "--base-url", self.base_url, "--revision", SHA, "--health-path", "/release.json", "--path", "/ok"
        )
        self.assertEqual(code, 0, output)
        self.assertIn("ok    revision 0123456789ab", output)
        self.assertIn("ok    GET", output)
        self.assertNotIn("do-not-print", output)

    def test_wrong_revision_fails_after_the_attempts(self) -> None:
        Handler.revision = OTHER
        code, output = self.run_smoke(
            "--base-url",
            self.base_url,
            "--revision",
            SHA,
            "--health-path",
            "/release.json",
            "--attempts",
            "3",
            "--interval",
            "0.5",
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.sleeps, [0.5, 0.5])
        self.assertIn(OTHER[:12], output)

    def test_failing_paths_and_redirects_fail(self) -> None:
        for path in ("/missing", "/moved"):
            with self.subTest(path=path):
                code, output = self.run_smoke(
                    "--base-url", self.base_url, "--revision", SHA, "--health-path", "/release.json", "--path", path
                )
                self.assertEqual(code, 1, output)
                self.assertIn("FAIL", output)

    def test_release_config_supplies_the_health_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "release.toml"
            config.write_text('health_path = "/release.json"\nrevision_key = "revision"\n')
            code, output = self.run_smoke(
                "--base-url", self.base_url, "--revision", SHA, "--release-config", str(config)
            )
        self.assertEqual(code, 0, output)

    def test_unsafe_inputs_exit_2(self) -> None:
        cases = [
            ("--base-url", "http://example.invalid", "--revision", SHA, "--health-path", "/h"),
            ("--base-url", "https://user:pw@example.invalid", "--revision", SHA, "--health-path", "/h"),
            ("--base-url", "ftp://example.invalid", "--revision", SHA, "--health-path", "/h"),
            ("--base-url", self.base_url, "--revision", "abc", "--health-path", "/h"),
            ("--base-url", self.base_url, "--revision", SHA),
            ("--base-url", self.base_url, "--revision", SHA, "--health-path", "/h", "--path", "relative"),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.run_smoke(*args)[0], 2)


if __name__ == "__main__":
    unittest.main()
