"""Deployment contracts v1: the examples are valid and each rule refuses its violation."""

from __future__ import annotations

import copy
import io
import json
import tomllib
import unittest
from contextlib import redirect_stderr

from support import DEPLOY_ASSETS, load_deploy_script

contracts = load_deploy_script("contracts.py")
EXAMPLES = DEPLOY_ASSETS / "contracts"


def load(name: str) -> dict:
    path = EXAMPLES / name
    if name.endswith(".toml"):
        return tomllib.loads(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


class ExamplesAreValid(unittest.TestCase):
    def test_every_example_passes_its_validator(self) -> None:
        self.assertEqual(contracts.validate_release_record(load("release-record.staging.json")), [])
        self.assertEqual(contracts.validate_release_record(load("release-record.production.json")), [])
        self.assertEqual(
            contracts.validate_promotion(load("release-record.staging.json"), load("release-record.production.json")),
            [],
        )
        self.assertEqual(contracts.validate_deployment_record(load("deployment-record.json")), [])
        self.assertEqual(contracts.validate_project_config(load("release.toml")), [])
        self.assertEqual(contracts.validate_settings_manifest(load("settings.toml")), [])
        defaults = (EXAMPLES / "production.env").read_text(encoding="utf-8")
        self.assertEqual(contracts.validate_defaults_file(defaults, load("settings.toml")), [])

    def test_command_line_reports_valid_and_invalid(self) -> None:
        self.assertEqual(contracts.main(["release-record", str(EXAMPLES / "release-record.staging.json")]), 0)
        self.assertEqual(contracts.main(["settings", str(EXAMPLES / "settings.toml")]), 0)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(contracts.main(["unknown"]), 2)


class ReleaseRecord(unittest.TestCase):
    def setUp(self) -> None:
        self.record = load("release-record.staging.json")

    def problems(self, **changes: object) -> list[str]:
        record = copy.deepcopy(self.record)
        record.update(changes)
        return contracts.validate_release_record(record)

    def test_images_and_bundle_must_be_pinned_by_digest(self) -> None:
        self.assertTrue(self.problems(images={"web": "ghcr.io/example-owner/example/web:latest"}))
        self.assertTrue(self.problems(bundle="ghcr.io/example-owner/example/bundle:main"))
        self.assertTrue(self.problems(images={}))

    def test_identity_fields_are_strict(self) -> None:
        self.assertTrue(self.problems(commit="0123abc"))
        self.assertTrue(self.problems(version="1.4.2"))
        self.assertTrue(self.problems(created="2026-10-19 12:00"))
        self.assertTrue(self.problems(source="http://github.com/example-owner/example"))
        self.assertTrue(self.problems(rollback="no"))
        self.assertTrue(self.problems(schema="af.release/v2"))

    def test_unknown_and_missing_fields_are_refused(self) -> None:
        self.assertTrue(self.problems(extra=1))
        record = copy.deepcopy(self.record)
        del record["bundle"]
        self.assertTrue(contracts.validate_release_record(record))

    def test_messages_never_echo_values(self) -> None:
        secret_looking = "ghcr.io/example-owner/example/web:SECRET-VALUE"
        for problem in self.problems(images={"web": secret_looking}):
            self.assertNotIn("SECRET-VALUE", problem)


class Promotion(unittest.TestCase):
    def test_production_must_promote_the_same_artifacts(self) -> None:
        staging = load("release-record.staging.json")
        production = load("release-record.production.json")
        rebuilt = copy.deepcopy(production)
        rebuilt["images"]["web"] = rebuilt["images"]["web"][:-1] + "9"
        self.assertTrue(contracts.validate_promotion(staging, rebuilt))
        unversioned = dict(production, version=None)
        self.assertTrue(contracts.validate_promotion(staging, unversioned))
        other_commit = dict(production, commit="f" * 40)
        self.assertTrue(contracts.validate_promotion(staging, other_commit))


class DeploymentRecord(unittest.TestCase):
    def test_result_and_exit_code_must_agree(self) -> None:
        record = load("deployment-record.json")
        self.assertTrue(contracts.validate_deployment_record(dict(record, exit_code=3)))
        self.assertEqual(contracts.validate_deployment_record(dict(record, result="stopped", exit_code=5)), [])
        self.assertTrue(contracts.validate_deployment_record(dict(record, trigger="ssh")))


class ProjectConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load("release.toml")

    def test_blue_green_needs_routes(self) -> None:
        config = dict(self.config, switch="blue-green", routes=[])
        self.assertTrue(contracts.validate_project_config(config))

    def test_blue_green_switches_the_health_service(self) -> None:
        config = dict(self.config, switch="blue-green")
        self.assertEqual(contracts.validate_project_config(config), [])
        config["routes"] = [{"host_setting": "PUBLIC_HOST", "service": "worker", "port": 8000}]
        self.assertIn("health service to be a routed service", "; ".join(contracts.validate_project_config(config)))

    def test_routes_and_health_name_a_service_and_its_container_port(self) -> None:
        for route in (
            {"host_setting": "PUBLIC_HOST", "port_setting": "HTTP_PORT"},
            {"host_setting": "PUBLIC_HOST", "service": "web", "port": 0},
            {"host_setting": "PUBLIC_HOST", "service": "web", "port": True},
            {"host_setting": "public_host", "service": "web", "port": 8000},
        ):
            with self.subTest(route=route):
                self.assertTrue(contracts.validate_project_config(dict(self.config, routes=[route])))
        health = dict(self.config["health"], port="8000")
        self.assertTrue(contracts.validate_project_config(dict(self.config, health=health)))

    def test_production_with_migrations_requires_backup(self) -> None:
        config = copy.deepcopy(self.config)
        config["environments"]["production"]["backup"] = "optional"
        self.assertTrue(contracts.validate_project_config(config))
        config["migrate"] = []
        self.assertEqual(contracts.validate_project_config(config), [])

    def test_an_image_target_names_a_build_stage(self) -> None:
        for target, valid in (("runtime", True), ("backup.v2", True), ("", False), ("a b", False), (3, False)):
            with self.subTest(target=target):
                config = copy.deepcopy(self.config)
                config["images"][0]["target"] = target
                self.assertEqual(contracts.validate_project_config(config) == [], valid)

    def test_standard_version_and_health_are_required(self) -> None:
        self.assertTrue(contracts.validate_project_config(dict(self.config, af_standard=2)))
        self.assertTrue(contracts.validate_project_config(dict(self.config, health={"service": "web"})))


class SettingsManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load("settings.toml")

    def test_a_name_has_exactly_one_source(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["placement"].append("DATABASE_PASSWORD")
        self.assertTrue(contracts.validate_settings_manifest(manifest))

    def test_host_generated_names_need_a_generator(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["generators"] = {}
        self.assertTrue(contracts.validate_settings_manifest(manifest))
        manifest["generators"] = {"SESSION_KEY": "random"}
        self.assertTrue(contracts.validate_settings_manifest(manifest))

    def test_production_assertions_cannot_check_secrets(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["prod_assert"] = {"DATABASE_PASSWORD": "x"}
        self.assertTrue(contracts.validate_settings_manifest(manifest))

    def test_committed_defaults_cannot_hold_other_sources(self) -> None:
        problems = contracts.validate_defaults_file("PUBLIC_HOST=example.com\nSESSION_KEY=abc\n", self.manifest)
        self.assertEqual(len(problems), 2)
        self.assertFalse(any("example.com" in p or "abc" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
