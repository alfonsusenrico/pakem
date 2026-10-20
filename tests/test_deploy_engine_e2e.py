"""Release engine end to end with real Docker, registry, and cosign (opt-in: AF_SANDBOX=1).

Builds a tiny web image per release, publishes and signs each release as CI will, and lets
af_engine deploy them on this machine: a first install, an upgrade with a backup, and a
broken release that is restored automatically. Everything is removed afterwards.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from engine_sandbox import ENABLED, SKIP_REASON, EngineSandbox


@unittest.skipUnless(ENABLED, SKIP_REASON)
class EngineEndToEnd(EngineSandbox):
    def setUp(self) -> None:
        super().setUp()
        self.config = self.make_host("production")

    def test_install_upgrade_and_automatic_restore(self) -> None:
        first, second, broken = "1" * 40, "2" * 40, "3" * 40
        self.publish(first, "2026-10-20T00:00:00Z")
        self.assertEqual(self.deploy(self.config)["result"], "deployed")
        self.assertEqual(self.served_revision("production"), first)

        self.publish(second, "2026-10-21T00:00:00Z")
        record = self.deploy(self.config)
        self.assertEqual(record["result"], "deployed")
        self.assertEqual(self.served_revision("production"), second)
        self.assertEqual(Path(record["backup"], "database.dump").read_text().strip(), "backup of sandbox-production")

        # The broken image serves another revision: the engine must notice and restore.
        self.publish(broken, "2026-10-22T00:00:00Z", image_revision="f" * 40)
        record = self.deploy(self.config)
        self.assertEqual((record["result"], record["exit_code"]), ("restored", 3))
        self.assertEqual(self.served_revision("production"), second)
        self.assertEqual(os.readlink(self.config.base / "current"), f"releases/{second}")


if __name__ == "__main__":
    unittest.main()
