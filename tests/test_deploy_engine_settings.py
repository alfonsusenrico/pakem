"""Release engine, settings stage: defaults, placement, secret files, generated secrets."""

from __future__ import annotations

import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_settings  # noqa: E402

EXAMPLES = DEPLOY_ASSETS / "contracts"
PLACEMENT = "PUBLIC_HOST=example.test\nHTTP_PORT=18081\nDATA_DIR=/srv/data/example\n"
DB_SECRET = b"db-secret-value-1"


class Host:
    """A bundle with the example manifest plus a private host directory."""

    def __init__(self, case: unittest.TestCase) -> None:
        root = Path(case.enterContext(tempfile.TemporaryDirectory()))
        self.bundle = root / "bundle"
        (self.bundle / "deploy/env").mkdir(parents=True)
        shutil.copy(EXAMPLES / "settings.toml", self.bundle / "deploy/settings.toml")
        shutil.copy(EXAMPLES / "production.env", self.bundle / "deploy/env/production.env")
        (self.bundle / "deploy/env/staging.env").write_text("LOG_LEVEL=debug\n")
        self.base = root / "base"
        self.secrets = self.base / "secrets"
        self.secrets.mkdir(parents=True, mode=0o700)
        self.base.chmod(0o700)
        self.placement = self.base / "placement.env"
        self.write_placement(PLACEMENT)
        af_settings.set_secret(self.secrets, "DATABASE_PASSWORD", DB_SECRET)
        self.output = self.base / "runtime.env"

    def write_placement(self, text: str, mode: int = 0o600) -> None:
        self.placement.write_text(text)
        self.placement.chmod(mode)

    def build(self, environment: str = "production", **kwargs: object) -> af_settings.Runtime:
        options = {"placement_file": self.placement, **kwargs}
        return af_settings.build_runtime(self.bundle, environment, self.secrets, self.output, **options)


class BuildRuntime(unittest.TestCase):
    def test_runtime_holds_values_and_secret_paths_but_no_secret_values(self) -> None:
        host = Host(self)
        runtime = host.build()
        text = host.output.read_text()
        self.assertIn("PUBLIC_HOST='example.test'", text)
        self.assertIn("LOG_LEVEL='info'", text)
        self.assertIn("DATABASE_PASSWORD_FILE='/run/secrets/database_password'", text)
        self.assertIn("SESSION_KEY_FILE='/run/secrets/session_key'", text)
        self.assertIn(f"AF_SECRETS_DIR='{host.secrets}'", text)
        self.assertNotIn(DB_SECRET.decode(), text)
        self.assertNotIn((host.secrets / "SESSION_KEY").read_text(), text)
        self.assertEqual(stat.S_IMODE(host.output.stat().st_mode), 0o600)
        self.assertEqual(runtime.generated, ["SESSION_KEY"])
        self.assertEqual(runtime.secret_names, ["DATABASE_PASSWORD", "SESSION_KEY"])

    def test_generated_secrets_are_created_once_and_kept(self) -> None:
        host = Host(self)
        host.build()
        first = (host.secrets / "SESSION_KEY").read_bytes()
        self.assertEqual(stat.S_IMODE((host.secrets / "SESSION_KEY").stat().st_mode), 0o640)
        self.assertEqual(host.build().generated, [])
        self.assertEqual((host.secrets / "SESSION_KEY").read_bytes(), first)
        self.assertGreaterEqual(len(first), 40)

    def test_missing_required_values_and_secrets_are_reported_by_name(self) -> None:
        host = Host(self)
        (host.secrets / "DATABASE_PASSWORD").unlink()
        host.write_placement("PUBLIC_HOST=example.test\nDATA_DIR=/srv/data\n")
        with self.assertRaises(af_settings.SettingsError) as raised:
            host.build()
        message = str(raised.exception)
        self.assertIn("DATABASE_PASSWORD: required secret file is missing", message)
        self.assertIn("HTTP_PORT: required value is missing", message)
        self.assertFalse(host.output.exists())

    def test_formats_production_assertions_and_pairs(self) -> None:
        host = Host(self)
        host.write_placement(PLACEMENT.replace("18081", "port"))
        with self.assertRaisesRegex(af_settings.SettingsError, "HTTP_PORT: does not match its format"):
            host.build()
        host.write_placement(PLACEMENT)
        with self.assertRaisesRegex(af_settings.SettingsError, "LOG_LEVEL: production requires"):
            (host.bundle / "deploy/env/production.env").write_text("LOG_LEVEL=debug\n")
            host.build()
        (host.bundle / "deploy/env/production.env").write_text("LOG_LEVEL=info\nSMTP_USER=mailer\n")
        with self.assertRaisesRegex(af_settings.SettingsError, "set all of them or none"):
            host.build()
        af_settings.set_secret(host.secrets, "SMTP_PASSWORD", b"smtp")
        host.build()
        # Each environment has its own secrets directory; staging here has no SMTP.
        (host.secrets / "SMTP_PASSWORD").unlink()
        self.assertEqual(host.build("staging").names[0], "DATA_DIR")

    def test_placement_is_private_and_limited_to_placed_names(self) -> None:
        host = Host(self)
        host.write_placement(PLACEMENT, mode=0o644)
        with self.assertRaisesRegex(af_settings.SettingsError, "readable by others"):
            host.build()
        host.write_placement(PLACEMENT + "LOG_LEVEL=debug\n")
        with self.assertRaisesRegex(af_settings.SettingsError, "does not place: LOG_LEVEL"):
            host.build()
        host.write_placement(PLACEMENT + "DATABASE_PASSWORD=inline\n")
        with self.assertRaisesRegex(af_settings.SettingsError, "DATABASE_PASSWORD") as raised:
            host.build()
        self.assertNotIn("inline", str(raised.exception))

    def test_push_mode_takes_placement_from_ci_values(self) -> None:
        host = Host(self)
        values = {"PUBLIC_HOST": "ci.example.test", "HTTP_PORT": "18082", "DATA_DIR": "/srv/ci", "OTHER": "ignored"}
        host.build(placement_file=None, placement_values=values)
        text = host.output.read_text()
        self.assertIn("PUBLIC_HOST='ci.example.test'", text)
        self.assertNotIn("OTHER", text)
        with self.assertRaisesRegex(af_settings.SettingsError, "not both"):
            host.build(placement_values=values)

    def test_values_that_cannot_be_written_literally_are_refused(self) -> None:
        host = Host(self)
        host.write_placement(PLACEMENT.replace("example.test", "it's.test"))
        with self.assertRaisesRegex(af_settings.SettingsError, "PUBLIC_HOST: must not contain a single quote"):
            host.build()
        host.write_placement(PLACEMENT.replace("example.test", "'quoted'"))
        with self.assertRaisesRegex(af_settings.SettingsError, "without quotes"):
            host.build()


class Secrets(unittest.TestCase):
    def test_secret_files_must_be_private(self) -> None:
        host = Host(self)
        for mode in (0o644, 0o660):
            (host.secrets / "DATABASE_PASSWORD").chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(af_settings.SettingsError, "mode 0640"):
                host.build()
        # A 0600 file from an older engine is opened up to the group, which containers join.
        (host.secrets / "DATABASE_PASSWORD").chmod(0o600)
        host.build()
        self.assertEqual(stat.S_IMODE((host.secrets / "DATABASE_PASSWORD").stat().st_mode), 0o640)
        host.secrets.chmod(0o755)
        with self.assertRaisesRegex(af_settings.SettingsError, "private directory"):
            host.build()

    def test_set_secret_validates_name_and_size(self) -> None:
        host = Host(self)
        for name, value in (("lower", b"x"), ("EMPTY", b""), ("HUGE", b"x" * (af_settings.MAX_SECRET_BYTES + 1))):
            with self.subTest(name=name), self.assertRaises(af_settings.SettingsError):
                af_settings.set_secret(host.secrets, name, value)
        af_settings.set_secret(host.secrets, "DATABASE_PASSWORD", b"rotated")
        self.assertEqual((host.secrets / "DATABASE_PASSWORD").read_bytes(), b"rotated")


if __name__ == "__main__":
    unittest.main()
