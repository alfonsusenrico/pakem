"""Settings through the release receiver (ADR 0015): signed settings requests on the host.

Requests are signed with real `ssh-keygen -Y` keys, as releases are; the host is the
temporary layout `af_receive.py authorize` writes for the release tests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from support import DEPLOY_ASSETS
from test_deploy_engine_sequence import PLACEMENT, SECRET_VALUE
from test_deploy_receive import DEVICE, DirectHost

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_receive  # noqa: E402
import contracts  # noqa: E402

NEW_SECRET = "a-new-secret-value-never-printed"


class SettingsHost(DirectHost):
    def settings_request(self, action: str, names: list[str], value: bytes = b"", *, kind: str | None = None,
                         with_values: bool = False, environment: str = "staging", key: Path | None = None,
                         device: str = DEVICE, project: str = "example", created: datetime | None = None,
                         signed_value: bytes | None = None) -> bytes:
        """The header a device signs, then the value; `signed_value` signs another value's digest."""
        digest = hashlib.sha256(value if signed_value is None else signed_value).hexdigest()
        request = {
            "schema": contracts.SETTINGS_REQUEST_SCHEMA, "action": action, "project": project,
            "environment": environment, "names": names, "kind": kind, "with_values": with_values,
            "created": (created or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"), "device": device,
            "digest": f"sha256:{digest}",
        }
        data = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        path = self.root / "settings-request.json"
        path.write_bytes(data)
        signature = path.with_name("settings-request.json.sig")
        signature.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key or self.key), "-n", af_receive.NAMESPACE, str(path)],
                       check=True, capture_output=True)
        return data + b"\n" + base64.b64encode(signature.read_bytes()) + b"\n" + value

    def settings(self, stdin: bytes, projects: tuple[str, ...] = ("example",)) -> tuple[int, str, dict | None]:
        code, output = self.serve("settings", stdin, projects)
        reply = None
        if code == 0:
            reply = json.loads(output.strip().splitlines()[-1])
        return code, output, reply

    def secret(self, name: str, environment: str = "staging") -> Path:
        return self.base(environment) / "secrets" / name

    def assert_refused(self, stdin: bytes, message: str, *values: str, **options) -> None:
        code, output, _ = self.settings(stdin, **options)
        self.assertEqual(code, 2, output)
        self.assertIn(message, output)
        for value in (NEW_SECRET, SECRET_VALUE, *values):
            self.assertNotIn(value, output)
        self.assertFalse(self.secret("API_TOKEN").exists())


class WriteTests(SettingsHost):
    def test_a_secret_is_stored_privately_and_never_echoed(self) -> None:
        code, output, reply = self.settings(self.settings_request("secret-set", ["API_TOKEN"], NEW_SECRET.encode()))
        self.assertEqual(code, 0, output)
        self.assertEqual(reply, {"action": "secret-set", "environment": "staging", "project": "example",
                                 "stored": ["API_TOKEN"]})
        self.assertEqual(self.secret("API_TOKEN").read_bytes(), NEW_SECRET.encode())
        self.assertEqual(stat.S_IMODE(self.secret("API_TOKEN").stat().st_mode), 0o640)
        self.assertNotIn(NEW_SECRET, output)

    def test_a_missing_secrets_directory_is_made_private(self) -> None:
        secrets = self.base("staging") / "secrets"
        for path in secrets.iterdir():
            path.unlink()
        secrets.rmdir()
        code, output, _ = self.settings(self.settings_request("secret-set", ["API_TOKEN"], NEW_SECRET.encode()))
        self.assertEqual(code, 0, output)
        self.assertEqual(stat.S_IMODE(secrets.stat().st_mode), 0o700)

    def test_placement_merges_and_keeps_other_names(self) -> None:
        placement = self.base("staging") / "placement.env"
        placement.write_text("# host facts\n" + PLACEMENT)
        placement.chmod(0o600)
        code, output, reply = self.settings(self.settings_request(
            "placement-set", ["HTTP_PORT", "BOT_NAME"], b"HTTP_PORT=18090\nBOT_NAME=helper\n"))
        self.assertEqual(code, 0, output)
        self.assertEqual(reply["stored"], ["HTTP_PORT", "BOT_NAME"])
        self.assertEqual(placement.read_text(), "# host facts\nPUBLIC_HOST=example.test\nHTTP_PORT=18090\n"
                                                "DATA_DIR=/srv/data/example\nBOT_NAME=helper\n")
        self.assertEqual(stat.S_IMODE(placement.stat().st_mode), 0o600)

    def test_placement_is_created_when_the_host_has_none(self) -> None:
        placement = self.base("staging") / "placement.env"
        placement.unlink()
        code, output, _ = self.settings(self.settings_request("placement-set", ["PUBLIC_HOST"], b"PUBLIC_HOST=a.test\n"))
        self.assertEqual(code, 0, output)
        self.assertEqual(placement.read_text(), "PUBLIC_HOST=a.test\n")
        self.assertEqual(stat.S_IMODE(placement.stat().st_mode), 0o600)

    def test_a_placement_value_the_engine_cannot_read_changes_nothing(self) -> None:
        placement = self.base("staging") / "placement.env"
        for text, message in ((b"PUBLIC_HOST='quoted'\n", "without quotes"), (b"PUBLIC_HOST=\n", "must not be empty"),
                              (b"PUBLIC_HOST=a b #c\n", "inline comments"), (b"OTHER=x\n", "do not name the signed")):
            with self.subTest(message=message):
                self.assert_refused(self.settings_request("placement-set", ["PUBLIC_HOST"], text), message)
                self.assertEqual(placement.read_text(), PLACEMENT)

    def test_a_token_is_generated_on_the_host(self) -> None:
        code, output, reply = self.settings(self.settings_request("secret-generate", ["API_TOKEN"], kind="token:32"))
        self.assertEqual(code, 0, output)
        self.assertEqual(reply["generated"], ["API_TOKEN"])
        token = self.secret("API_TOKEN").read_text()
        self.assertEqual(len(token), 43)
        self.assertNotIn(token, output)

    def test_a_deploy_key_returns_only_its_public_half(self) -> None:
        code, output, reply = self.settings(self.settings_request("deploy-key", ["REPO_KEY"], kind="read"))
        self.assertEqual(code, 0, output)
        public = reply["public_key"]
        self.assertTrue(public.startswith("ssh-ed25519 "), public)
        self.assertTrue(public.endswith(" example-staging-REPO_KEY"), public)
        private = self.secret("REPO_KEY").read_text()
        self.assertIn("BEGIN OPENSSH PRIVATE KEY", private)
        self.assertNotIn("PRIVATE KEY", output)
        copy = self.root / "stored-key"
        copy.write_text(private)
        copy.chmod(0o600)
        derived = subprocess.run(["ssh-keygen", "-y", "-f", str(copy)], capture_output=True, check=True)
        self.assertEqual(derived.stdout.decode().split()[:2], public.split()[:2])
        self.assertEqual([path.name for path in self.base("staging").iterdir() if path.name.startswith(".deploy-key")], [])

    def test_a_signed_write_runs_once(self) -> None:
        signed = self.settings_request("secret-set", ["API_TOKEN"], NEW_SECRET.encode())
        self.assertEqual(self.settings(signed)[0], 0)
        code, output, _ = self.settings(signed)
        self.assertEqual(code, 2)
        self.assertIn("already ran here", output)

    def test_a_write_waits_for_no_deployment(self) -> None:
        lock = af_engine.acquire_lock(self.base("staging"), 0)
        try:
            code, output, _ = self.settings(self.settings_request("secret-set", ["API_TOKEN"], NEW_SECRET.encode()))
        finally:
            os.close(lock)
        self.assertEqual(code, af_engine.EXIT_BUSY, output)
        self.assertFalse(self.secret("API_TOKEN").exists())


class ListTests(SettingsHost):
    def test_list_names_set_and_missing_with_ages_and_values_only_when_asked(self) -> None:
        names = ["DATABASE_PASSWORD", "API_TOKEN", "PUBLIC_HOST"]
        code, output, reply = self.settings(self.settings_request("list", names))
        self.assertEqual(code, 0, output)
        self.assertTrue(reply["secrets"]["DATABASE_PASSWORD"]["set"])
        self.assertGreaterEqual(reply["secrets"]["DATABASE_PASSWORD"]["age_seconds"], 0)
        self.assertEqual(reply["secrets"]["API_TOKEN"], {"set": False, "age_seconds": None})
        self.assertEqual(reply["placement"]["PUBLIC_HOST"], {"set": True})
        self.assertNotIn(SECRET_VALUE, output)
        self.assertNotIn("example.test", output)
        code, output, reply = self.settings(self.settings_request("list", names, with_values=True))
        self.assertEqual(reply["placement"]["PUBLIC_HOST"], {"set": True, "value": "example.test"})
        self.assertNotIn("value", reply["secrets"]["DATABASE_PASSWORD"])
        self.assertNotIn(SECRET_VALUE, output)

    def test_list_may_run_while_a_deployment_holds_the_lock(self) -> None:
        lock = af_engine.acquire_lock(self.base("staging"), 0)
        try:
            code, output, _ = self.settings(self.settings_request("list", ["PUBLIC_HOST"]))
        finally:
            os.close(lock)
        self.assertEqual(code, 0, output)


class SettingsRefusalTests(SettingsHost):
    def value_request(self, **options) -> bytes:
        return self.settings_request("secret-set", ["API_TOKEN"], NEW_SECRET.encode(), **options)

    def test_a_forged_signature_is_refused(self) -> None:
        other = self.make_key("other-key")
        self.assert_refused(self.value_request(key=other), "not signed by device laptop's key")

    def test_a_tampered_header_is_refused(self) -> None:
        signed = self.value_request()
        self.assert_refused(signed.replace(b'"API_TOKEN"', b'"API_TOKEM"', 1), "not signed by device laptop's key")

    def test_the_principal_is_the_project(self) -> None:
        # The forced command lists both projects, but this device's signer line only one.
        self.assert_refused(self.value_request(project="other"), "not signed by device laptop's key",
                            projects=("example", "other"))
        self.assert_refused(self.value_request(project="other"), "may not release other")

    def test_another_device_is_refused(self) -> None:
        self.assert_refused(self.value_request(device="desktop"), "names another device")

    def test_a_stale_or_future_request_is_refused(self) -> None:
        for moment in (datetime.now(UTC) - timedelta(minutes=11), datetime.now(UTC) + timedelta(minutes=5)):
            with self.subTest(moment=moment):
                self.assert_refused(self.value_request(created=moment), "not fresh")

    def test_the_value_must_match_its_signed_digest(self) -> None:
        self.assert_refused(self.value_request(signed_value=b"another value"), "does not match the signed request")
        self.assert_refused(self.settings_request("list", ["API_TOKEN"], NEW_SECRET.encode()), "sends no value")

    def test_names_must_be_upper_case_and_well_formed(self) -> None:
        for names in (["api_token"], ["../API_TOKEN"], ["API_TOKEN", "API_TOKEN"], [], ["A", "B"]):
            with self.subTest(names=names):
                self.assert_refused(self.settings_request("secret-set", names, NEW_SECRET.encode()), "settings request")

    def test_kinds_are_checked(self) -> None:
        for action, kind in (("secret-generate", "token:8"), ("secret-generate", "deploy-key:read"),
                             ("deploy-key", "admin"), ("secret-set", "token:32")):
            with self.subTest(action=action, kind=kind):
                value = NEW_SECRET.encode() if action == "secret-set" else b""
                self.assert_refused(self.settings_request(action, ["API_TOKEN"], value, kind=kind), "kind")

    def test_an_unknown_environment_is_refused(self) -> None:
        self.assert_refused(self.value_request(environment="preview"), "no direct preview environment")

    def test_failures_name_settings_never_values(self) -> None:
        secrets = self.base("staging") / "secrets"
        secrets.chmod(0o755)
        try:
            code, output, _ = self.settings(self.value_request())
        finally:
            secrets.chmod(0o700)
        self.assertEqual(code, 2, output)
        self.assertIn("secrets directory must be a private directory", output)
        self.assertNotIn(NEW_SECRET, output)
        earlier = datetime.now(UTC) - timedelta(seconds=2)
        with mock.patch.object(af_receive.af_settings, "set_secret", side_effect=OSError("disk full")):
            code, output, _ = self.settings(self.value_request(created=earlier))
        self.assertEqual(code, 1, output)
        self.assertNotIn(NEW_SECRET, output)

    def test_an_oversized_value_is_refused(self) -> None:
        large = b"x" * (64 * 1024 + 1)
        self.assert_refused(self.settings_request("secret-set", ["API_TOKEN"], large), "larger than a setting may be")


if __name__ == "__main__":
    unittest.main()
