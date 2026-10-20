"""Release triggers (af_trigger.py): the pull agent's decisions and the push receiver's grammar."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_engine  # noqa: E402
import af_notify  # noqa: E402
import af_oci  # noqa: E402
import af_trigger  # noqa: E402

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


class FakeRegistry:
    def __init__(self, digests: dict[str, str | Exception]) -> None:
        self.digests = digests

    def resolve(self, reference: af_oci.Reference) -> str:
        result = self.digests[f"{reference.repository}:{reference.tag}"]
        if isinstance(result, Exception):
            raise result
        return result


class FakeEngine:
    """Stands in for af_engine.Engine; records each construction and returns a scripted outcome."""

    def __init__(self, outcome: dict | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def __call__(self, host: af_engine.HostConfig, **options: object) -> FakeEngine:
        self.calls.append({"host": host, **options})
        return self

    def deploy(self) -> dict:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def deployed(result: str = "deployed", exit_code: int = 0) -> dict:
    return {"result": result, "exit_code": exit_code, "commit": "1" * 40}


class Hosts:
    """Host configs and bases in a temporary directory."""

    def __init__(self, case: unittest.TestCase) -> None:
        self.root = Path(case.enterContext(tempfile.TemporaryDirectory()))
        self.configs = self.root / "hosts"
        self.configs.mkdir()

    def add(self, project: str, environment: str) -> af_engine.HostConfig:
        base = self.root / "srv" / project / environment
        base.mkdir(parents=True, mode=0o700)
        path = self.configs / f"{project}-{environment}.toml"
        path.write_text(
            textwrap.dedent(
                f"""\
                project = "{project}"
                environment = "{environment}"
                base = "{base}"
                pointer = "ghcr.io/owner/{project}:release-{environment}"
                public_key = "/etc/af/keys/{project}.pub"
                """
            )
        )
        return af_engine.HostConfig.load(path)


def quietly(function, *arguments, **options):
    output = io.StringIO()
    with redirect_stdout(output):
        result = function(*arguments, **options)
    return result, output.getvalue()


class PullAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.hosts = Hosts(self)
        self.now = 1_000_000.0

    def agent(self, engine: FakeEngine, digests: dict) -> af_trigger.Agent:
        registry = FakeRegistry(digests)
        self.sent: list[tuple[str, str]] = []
        notifier = af_notify.Notifier([])
        notifier.send = lambda event, text, record=None: self.sent.append((event, text))
        return af_trigger.Agent(
            engine=engine, registry=lambda host: registry, notifier=lambda host: notifier, clock=lambda: self.now
        )

    def test_a_release_already_attempted_is_left_alone(self) -> None:
        host = self.hosts.add("example", "staging")
        af_engine.write_json(host.base / "deployment.json", {"release": DIGEST_A, "result": "failed"})
        engine = FakeEngine(deployed())
        status, _ = quietly(self.agent(engine, {"owner/example:release-staging": DIGEST_A}).poll, self.hosts.configs)
        self.assertEqual(status, 0)
        self.assertEqual(engine.calls, [])

    def test_a_moved_pointer_runs_the_engine_as_the_agent(self) -> None:
        host = self.hosts.add("example", "staging")
        af_engine.write_json(host.base / "deployment.json", {"release": DIGEST_A})
        engine = FakeEngine(deployed())
        status, output = quietly(self.agent(engine, {"owner/example:release-staging": DIGEST_B}).poll, self.hosts.configs)
        self.assertEqual(status, 0)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual((engine.calls[0]["trigger"], engine.calls[0]["actor"]), ("pull", "agent"))
        self.assertEqual(engine.calls[0]["host"].environment, "staging")
        self.assertIn("deployed 111111111111", output)

    def test_a_first_deployment_needs_no_previous_record(self) -> None:
        self.hosts.add("example", "production")
        engine = FakeEngine(deployed())
        status, _ = quietly(self.agent(engine, {"owner/example:release-production": DIGEST_A}).poll, self.hosts.configs)
        self.assertEqual((status, len(engine.calls)), (0, 1))

    def test_a_failed_or_restored_release_reports_attention(self) -> None:
        self.hosts.add("example", "staging")
        engine = FakeEngine(deployed("restored", 3))
        status, _ = quietly(self.agent(engine, {"owner/example:release-staging": DIGEST_A}).poll, self.hosts.configs)
        self.assertEqual(status, 1)

    def test_a_refusal_is_retried_only_after_the_back_off(self) -> None:
        host = self.hosts.add("example", "staging")
        engine = FakeEngine(af_engine.EngineError("setting DATABASE_PASSWORD is missing", af_engine.EXIT_REFUSED))
        agent = self.agent(engine, {"owner/example:release-staging": DIGEST_A})
        status, output = quietly(agent.poll, self.hosts.configs)
        self.assertEqual(status, 1)
        self.assertIn("refused: setting DATABASE_PASSWORD is missing", output)
        self.assertEqual(json.loads((host.base / "agent.json").read_text())["refused"], DIGEST_A)

        self.now += af_trigger.REFUSED_RETRY_SECONDS - 1
        status, output = quietly(agent.poll, self.hosts.configs)
        self.assertEqual((status, output, len(engine.calls)), (1, "", 1))

        self.assertEqual([event for event, _ in self.sent], ["refused"])
        self.assertIn("example staging: refused sha256:aaaa", self.sent[0][1])

        self.now += 2
        quietly(agent.poll, self.hosts.configs)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(len(self.sent), 1)  # the same refusal is announced once

        self.now += af_trigger.REFUSED_RETRY_SECONDS + 1
        engine.outcome = deployed()
        status, _ = quietly(agent.poll, self.hosts.configs)
        self.assertEqual((status, len(engine.calls)), (0, 3))
        self.assertFalse((host.base / "agent.json").exists())

    def test_a_new_pointer_is_tried_at_once_even_after_a_refusal(self) -> None:
        host = self.hosts.add("example", "staging")
        af_engine.write_json(host.base / "agent.json", {"refused": DIGEST_A, "refused_at": self.now})
        engine = FakeEngine(deployed())
        quietly(self.agent(engine, {"owner/example:release-staging": DIGEST_B}).poll, self.hosts.configs)
        self.assertEqual(len(engine.calls), 1)

    def test_a_busy_lock_waits_for_the_next_poll_without_back_off(self) -> None:
        host = self.hosts.add("example", "staging")
        engine = FakeEngine(af_engine.EngineError("another deployment holds the lock", af_engine.EXIT_BUSY))
        status, _ = quietly(self.agent(engine, {"owner/example:release-staging": DIGEST_A}).poll, self.hosts.configs)
        self.assertEqual(status, 0)
        self.assertFalse((host.base / "agent.json").exists())

    def test_one_broken_host_does_not_stop_the_others(self) -> None:
        self.hosts.add("alpha", "staging")
        self.hosts.add("beta", "staging")
        (self.hosts.configs / "broken.toml").write_text("project = \n")
        engine = FakeEngine(deployed())
        digests = {
            "owner/alpha:release-staging": af_oci.RegistryError("registry answered 503"),
            "owner/beta:release-staging": DIGEST_A,
        }
        status, output = quietly(self.agent(engine, digests).poll, self.hosts.configs)
        self.assertEqual(status, 1)
        self.assertEqual([call["host"].project for call in engine.calls], ["beta"])
        self.assertIn("alpha-staging: release pointer unavailable", output)
        self.assertIn("broken.toml: unusable host config", output)


class PushReceiver(unittest.TestCase):
    def setUp(self) -> None:
        self.hosts = Hosts(self)
        self.host = self.hosts.add("example", "staging")
        self.hosts.add("example", "production")
        self.hosts.add("other", "staging")

    def receive(self, command: str | None, environments: list[str] | None = None, outcome: dict | None = None):
        engine = FakeEngine(outcome or deployed())
        status, output = quietly(
            af_trigger.receive, self.hosts.configs, "example", environments or [], command, engine=engine
        )
        return status, output, engine

    def test_deploy_runs_the_engine_as_a_push_with_the_actor(self) -> None:
        status, _, engine = self.receive("deploy staging octocat")
        self.assertEqual(status, 0)
        self.assertEqual((engine.calls[0]["trigger"], engine.calls[0]["actor"]), ("push", "octocat"))
        self.assertEqual(engine.calls[0]["host"].project, "example")

    def test_the_engine_exit_code_is_the_reply(self) -> None:
        status, _, _ = self.receive("deploy staging", outcome=deployed("restored", 3))
        self.assertEqual(status, 3)

    def test_status_prints_the_deployment_record(self) -> None:
        af_engine.write_json(self.host.base / "deployment.json", {"result": "deployed", "release": DIGEST_A})
        status, output, engine = self.receive("status staging")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["release"], DIGEST_A)
        self.assertEqual(engine.calls, [])

    def test_anything_but_the_grammar_is_refused(self) -> None:
        commands = [
            None,
            "",
            "deploy",
            "deploy staging octocat extra",
            "status staging octocat",
            "rollback staging",
            "deploy staging; rm -rf /",
            "deploy ../staging",
            "deploy Staging",
            "deploy staging -oProxyCommand=x",
            "deploy staging " + "a" * 300,
            "deploy staging octocät",
            "sh -c id",
        ]
        for command in commands:
            with self.subTest(command=command):
                status, _, engine = self.receive(command)
                self.assertEqual(status, af_engine.EXIT_REFUSED)
                self.assertEqual(engine.calls, [])

    def test_a_key_limited_to_environments_cannot_reach_others(self) -> None:
        status, output, engine = self.receive("deploy production", environments=["staging"])
        self.assertEqual(status, af_engine.EXIT_REFUSED)
        self.assertIn("may not trigger production", output)
        self.assertEqual(engine.calls, [])

    def test_a_project_key_reaches_only_its_own_configs(self) -> None:
        status, output, _ = self.receive("deploy review")
        self.assertEqual(status, af_engine.EXIT_REFUSED)
        self.assertIn("no host config for example review", output)

    def test_a_config_naming_another_project_is_refused(self) -> None:
        mislabeled = self.hosts.configs / "example-review.toml"
        mislabeled.write_text((self.hosts.configs / "other-staging.toml").read_text())
        status, output, engine = self.receive("deploy review")
        self.assertEqual(status, af_engine.EXIT_REFUSED)
        self.assertIn("names another project or environment", output)
        self.assertEqual(engine.calls, [])

    def test_an_invalid_project_option_is_refused(self) -> None:
        status, _ = quietly(af_trigger.main, ["receive", "--config-dir", str(self.hosts.configs), "--project", "../x"])
        self.assertEqual(status, af_engine.EXIT_REFUSED)


if __name__ == "__main__":
    unittest.main()
