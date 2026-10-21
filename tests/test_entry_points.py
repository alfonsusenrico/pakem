"""The `pakem` command and its short alias `pk` are the same entry point."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from support import PAKEM, REPO_ROOT, IsolatedTestCase, isolated_env

PK = REPO_ROOT / "tools" / "pk"


class EntryPointTests(IsolatedTestCase):
    def run_entry(self, entry, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(entry), "--root", str(self.framework), *args],
            capture_output=True,
            text=True,
            env=isolated_env(self.home),
            cwd=str(self.base),
        )

    def test_pk_is_an_identical_executable_copy_of_pakem(self) -> None:
        self.assertEqual(PK.read_bytes(), PAKEM.read_bytes())
        self.assertTrue(os.access(PK, os.X_OK))
        self.assertTrue(os.access(PAKEM, os.X_OK))

    def test_pk_runs_the_same_commands(self) -> None:
        for entry in (PAKEM, PK):
            with self.subTest(entry=entry.name):
                completed = self.run_entry(entry, "lint")
                self.assert_ok(completed)
                self.assertIn("pakem lint", completed.stdout)

    def test_no_entry_point_under_the_former_command_name_remains(self) -> None:
        former = [path.name for path in (REPO_ROOT / "tools").iterdir() if path.name.split("_")[0] == "af"]
        self.assertEqual(former, [])


if __name__ == "__main__":
    unittest.main()
