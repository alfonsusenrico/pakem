"""Unit tests for the deploy asset materialize_runtime.py."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import DEPLOY_ASSETS, load_deploy_script

materialize_runtime = load_deploy_script("materialize_runtime.py")

SECRET = "postgresql://app:pa$$word with space@db/app"
MANIFEST = textwrap.dedent(
    """\
    required = ["DATABASE_URL", "SESSION_SECRET"]
    optional = ["SENTRY_DSN", "SMTP_USER", "SMTP_PASSWORD", "APP_DEBUG"]
    pairs = [["SMTP_USER", "SMTP_PASSWORD"]]
    host_generated = ["APP_SIGNING_KEY"]

    [defaults]
    staging = "deploy/runtime/staging.env"
    production = "deploy/runtime/production.env"

    [formats]
    DATABASE_URL = "postgresql://.+"

    [prod_assert]
    SESSION_COOKIE_SECURE = "true"
    APP_DEBUG = "false"
    """
)
STAGING_DEFAULTS = "# staging\nAPP_DEBUG=true\nSESSION_COOKIE_SECURE=false\nHTTP_PORT=18080\n"
PRODUCTION_DEFAULTS = "APP_DEBUG=false\nSESSION_COOKIE_SECURE=true\nHTTP_PORT=18081\n"


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="af-materialize-")
        self.root = Path(self._temporary.name)
        self.write("deploy/runtime.manifest.toml", MANIFEST)
        self.write("deploy/runtime/staging.env", STAGING_DEFAULTS)
        self.write("deploy/runtime/production.env", PRODUCTION_DEFAULTS)
        self.output = self.root / "out" / "runtime.env"
        self.output.parent.mkdir()
        self.environ = {"DATABASE_URL": SECRET, "SESSION_SECRET": "s3cret-session-value", "UNRELATED": "ignored"}

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_tool(self, environment: str = "staging", *extra: str) -> tuple[int, str]:
        output = io.StringIO()
        argv = ["--root", str(self.root), "--environment", environment, "--output", str(self.output), *extra]
        with redirect_stdout(output), redirect_stderr(output):
            code = materialize_runtime.main(argv, dict(self.environ))
        return code, output.getvalue()

    def runtime(self) -> dict[str, str]:
        values = {}
        for line in self.output.read_text().splitlines():
            if line.startswith("#"):
                continue
            name, _, quoted = line.partition("=")
            self.assertTrue(quoted.startswith("'") and quoted.endswith("'"), line)
            values[name] = quoted[1:-1]
        return values

    def test_writes_an_owner_only_single_quoted_runtime(self) -> None:
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(stat.S_IMODE(os.lstat(self.output).st_mode), 0o600)
        values = self.runtime()
        self.assertEqual(values["DATABASE_URL"], SECRET)
        self.assertEqual(values["HTTP_PORT"], "18080")
        self.assertNotIn("UNRELATED", values)
        self.assertNotIn("APP_SIGNING_KEY", values)
        self.assertIn("# af:host-generated APP_SIGNING_KEY", self.output.read_text())
        self.assertNotIn(SECRET, output)
        self.assertNotIn("s3cret-session-value", output)

    def test_ci_values_override_optional_defaults_and_json_sources_win(self) -> None:
        self.environ["APP_DEBUG"] = "from-env"
        self.environ["AF_SECRETS"] = json.dumps(
            {"APP_DEBUG": "from-json", "SESSION_SECRET": "json-secret", "github_token": "x"}
        )
        code, output = self.run_tool("staging", "--values-json-env", "AF_SECRETS")
        self.assertEqual(code, 0, output)
        values = self.runtime()
        self.assertEqual(values["APP_DEBUG"], "from-json")
        self.assertEqual(values["SESSION_SECRET"], "json-secret")
        self.assertNotIn("github_token", values)

    def test_missing_required_values_fail_and_write_nothing(self) -> None:
        del self.environ["SESSION_SECRET"]
        self.environ["DATABASE_URL"] = ""
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("missing required variables: DATABASE_URL, SESSION_SECRET", output)
        self.assertFalse(self.output.exists())

    def test_rejected_values_are_named_but_never_shown(self) -> None:
        cases = {
            "multi-line": "line one\nline two",
            "single quote": "it's",
            "edge whitespace": " padded",
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.environ["SESSION_SECRET"] = value
                code, output = self.run_tool()
                self.assertEqual(code, 1)
                self.assertIn("SESSION_SECRET", output)
                self.assertNotIn(value.strip(), output)

    def test_format_rules_apply_without_showing_values(self) -> None:
        self.environ["DATABASE_URL"] = "mysql://secret-host/db"
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("DATABASE_URL does not match its format", output)
        self.assertNotIn("secret-host", output)

    def test_pairs_must_be_complete(self) -> None:
        self.environ["SMTP_USER"] = "mailer"
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("SMTP_USER, SMTP_PASSWORD must be set together (missing SMTP_PASSWORD)", output)
        self.environ["SMTP_PASSWORD"] = "pw"
        self.assertEqual(self.run_tool()[0], 0)

    def test_host_generated_values_must_not_come_from_ci(self) -> None:
        self.environ["APP_SIGNING_KEY"] = "from-ci"
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("APP_SIGNING_KEY is host-generated", output)

    def test_secrets_must_not_be_committed_as_defaults(self) -> None:
        self.write("deploy/runtime/staging.env", STAGING_DEFAULTS + "SESSION_SECRET=committed\n")
        code, output = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("SESSION_SECRET must not appear in the committed defaults", output)

    def test_production_assertions_apply_only_to_production(self) -> None:
        self.assertEqual(self.run_tool("staging")[0], 0)
        self.assertEqual(self.run_tool("production")[0], 0)
        self.environ["APP_DEBUG"] = "true"
        code, output = self.run_tool("production")
        self.assertEqual(code, 1)
        self.assertIn("production assertion failed: APP_DEBUG must be 'false'", output)

    def test_invalid_manifests_and_defaults_exit_2(self) -> None:
        cases = {
            "unknown key": ("deploy/runtime.manifest.toml", MANIFEST.replace("pairs =", "extras = []\npairs =")),
            "overlap": (
                "deploy/runtime.manifest.toml",
                MANIFEST.replace('optional = ["SENTRY_DSN"', 'optional = ["SESSION_SECRET", "SENTRY_DSN"'),
            ),
            "bad regex": ("deploy/runtime.manifest.toml", MANIFEST.replace('"postgresql://.+"', '"(unclosed"')),
            "quoted default": ("deploy/runtime/staging.env", 'APP_DEBUG="true"\n'),
            "inline comment": ("deploy/runtime/staging.env", "APP_DEBUG=true # on\n"),
            "export": ("deploy/runtime/staging.env", "export APP_DEBUG=true\n"),
        }
        for label, (relative, text) in cases.items():
            with self.subTest(label):
                original = (self.root / relative).read_text()
                self.write(relative, text)
                try:
                    self.assertEqual(self.run_tool()[0], 2)
                finally:
                    self.write(relative, original)

    def test_unknown_environment_exits_2(self) -> None:
        code, output = self.run_tool("qa")
        self.assertEqual(code, 2)
        self.assertIn("no defaults file for environment 'qa'", output)

    def test_example_manifest_is_valid(self) -> None:
        self.write("deploy/runtime.manifest.toml", (DEPLOY_ASSETS / "runtime.manifest.example.toml").read_text())
        manifest = materialize_runtime.load_manifest(self.root / "deploy" / "runtime.manifest.toml", self.root)
        self.assertEqual(manifest.host_generated, ["APP_SIGNING_KEY"])
        self.assertIn("production", manifest.defaults)


if __name__ == "__main__":
    unittest.main()
