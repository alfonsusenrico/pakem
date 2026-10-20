"""Triggers and the health monitor end to end with real Docker, registry, and cosign
(opt-in: AF_SANDBOX=1).

One signed release is pointed at staging and production. The pull agent deploys staging,
the push receiver deploys production, and the two deployment records must agree on
everything but the environment, the trigger, and the times. The monitor then notices a
stopped release and its recovery. Everything is removed afterwards.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

from engine_sandbox import ENABLED, SKIP_REASON, EngineSandbox
from support import DEPLOY_ASSETS

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_monitor  # noqa: E402
import af_notify  # noqa: E402
import af_trigger  # noqa: E402

PER_ATTEMPT = {"environment", "trigger", "actor", "started", "finished", "backup"}


@unittest.skipUnless(ENABLED, SKIP_REASON)
class TriggersEndToEnd(EngineSandbox):
    def test_pull_and_push_deploy_the_same_release_the_same_way(self) -> None:
        commit = "4" * 40
        staging, production = self.make_host("staging"), self.make_host("production")
        pull_configs, push_configs = self.work / "pull", self.work / "push"
        self.write_config(staging, pull_configs)
        self.write_config(production, push_configs)
        self.publish(commit, "2026-10-20T00:00:00Z", environments=("staging", "production"))

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(af_trigger.Agent().poll(pull_configs), 0)
            # A second pass finds nothing new and leaves the engine alone.
            self.assertEqual(af_trigger.Agent().poll(pull_configs), 0)
            status = af_trigger.receive(push_configs, "sandbox", ["production"], "deploy production ci-bot")
        self.assertEqual(status, 0)
        self.assertNotIn("sandbox-secret", output.getvalue())

        self.assertEqual(self.served_revision("staging"), commit)
        self.assertEqual(self.served_revision("production"), commit)
        self.assertEqual(len((staging.base / "deployments.jsonl").read_text().splitlines()), 1)

        pulled = json.loads((staging.base / "deployment.json").read_text())
        pushed = json.loads((production.base / "deployment.json").read_text())
        self.assertEqual((pulled["result"], pulled["trigger"], pulled["actor"]), ("deployed", "pull", "agent"))
        self.assertEqual((pushed["result"], pushed["trigger"], pushed["actor"]), ("deployed", "push", "ci-bot"))
        self.assertEqual(
            {key: value for key, value in pulled.items() if key not in PER_ATTEMPT},
            {key: value for key, value in pushed.items() if key not in PER_ATTEMPT},
        )


    def test_the_monitor_notices_a_stopped_release_and_its_recovery(self) -> None:
        commit = "5" * 40
        staging = self.make_host("staging")
        configs = self.work / "hosts"
        self.write_config(staging, configs)
        self.publish(commit, "2026-10-20T00:00:00Z", environments=("staging",))
        self.assertEqual(self.deploy(staging)["result"], "deployed")

        sent: list[str] = []
        notifier = af_notify.Notifier([])
        notifier.send = lambda event, text, record=None: sent.append(event)
        monitor = af_monitor.Monitor(notifier=lambda host: notifier)
        compose = ["docker", "compose", "--project-name", "sandbox-staging"]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(monitor.check_all(configs), 0)
            subprocess.run([*compose, "stop"], capture_output=True, check=True)
            self.assertEqual([monitor.check_all(configs), monitor.check_all(configs)], [1, 1])
            subprocess.run([*compose, "start"], capture_output=True, check=True)
            recovered = monitor.check_all(configs)
        self.assertEqual(recovered, 0)
        self.assertEqual(sent, ["unhealthy", "recovered"])


if __name__ == "__main__":
    unittest.main()
