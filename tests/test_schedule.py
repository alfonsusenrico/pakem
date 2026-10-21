"""pakem schedule: launchd plist generation, install/uninstall bookkeeping, status, platform refusal.

These tests call the schedule module in-process with HOME pointing at a temporary
directory and an injected launchctl runner, so no real job is ever loaded.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from support import REPO_ROOT, IsolatedTestCase, write

sys.path.insert(0, str(REPO_ROOT / "tools"))

from pakem_lib import schedule  # noqa: E402
from pakem_lib.cli import build_parser  # noqa: E402
from pakem_lib.common import PakemError  # noqa: E402

UID = 501
PYTHON = "/usr/local/bin/python3-for-tests"
LABEL = "com.pakem.drift-report"
SAMPLE_PRINT = """gui/501/com.pakem.drift-report = {
\tactive count = 0
\tpath = /var/empty/Library/LaunchAgents/com.pakem.drift-report.plist
\tstate = not running
\tprogram = /usr/bin/python3
\truns = 2
\tlast exit code = 0
\tendpoints = {
\t\tstate = active
\t}
}
"""


class FakeLaunchctl:
    """Records launchctl arguments and answers with configured exit codes."""

    def __init__(self, returncodes: dict[str, int] | None = None, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncodes = returncodes or {}
        self.stdout = stdout

    def __call__(self, arguments: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(arguments))
        code = self.returncodes.get(arguments[0], 0)
        stderr = "" if code == 0 else "Boot-out failed: 3: No such process"
        return subprocess.CompletedProcess(["launchctl", *arguments], code, stdout=self.stdout, stderr=stderr)


def missing_launchctl(arguments: list[str]) -> subprocess.CompletedProcess:
    raise FileNotFoundError("launchctl")


class ScheduleTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.launchctl = FakeLaunchctl()
        self.plist = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        self.log = self.home / ".local" / "state" / "pakem" / "reports" / "launchd.log"
        self.manifest_path = self.home / ".local" / "state" / "pakem" / "manifest.json"

    def context(self, platform: str = "darwin", runner=None) -> schedule.ScheduleContext:
        return schedule.ScheduleContext(
            platform=platform, uid=UID, runner=runner or self.launchctl, python=PYTHON
        )

    def run_schedule(self, *arguments: str, context: schedule.ScheduleContext | None = None) -> tuple[int, str]:
        args = build_parser().parse_args(["--root", str(self.framework), "schedule", *arguments])
        output = io.StringIO()
        with redirect_stdout(output):
            code = schedule.run_schedule(args, context or self.context())
        return code, output.getvalue()

    def manifest_record(self) -> dict | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text())["schedule"].get(LABEL)

    def expected_job(self, weekday: int = 1, hour: int = 8, minute: int = 0) -> dict:
        return {
            "Label": LABEL,
            "ProgramArguments": [PYTHON, str(self.framework / "tools" / "pakem"), "report"],
            "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
            "StandardOutPath": str(self.log),
            "StandardErrorPath": str(self.log),
            "RunAtLoad": False,
        }

    def test_dry_run_prints_plist_and_commands_only(self) -> None:
        code, output = self.run_schedule("install")
        self.assertEqual(code, 0)
        start = output.index("<?xml")
        end = output.index("</plist>") + len("</plist>")
        self.assertEqual(plistlib.loads(output[start:end].encode()), self.expected_job())
        self.assertIn(f"run: launchctl bootstrap gui/{UID} {self.plist}", output)
        self.assertIn("Dry run: nothing written and launchctl not run.", output)
        self.assertEqual(self.launchctl.calls, [])
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.manifest_path.exists())

    def test_apply_writes_plist_bootstraps_and_records(self) -> None:
        code, output = self.run_schedule("install", "--weekday", "5", "--hour", "17", "--minute", "45", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(plistlib.loads(self.plist.read_bytes()), self.expected_job(5, 17, 45))
        self.assertEqual(self.launchctl.calls, [["bootstrap", f"gui/{UID}", str(self.plist)]])
        self.assertTrue(self.log.parent.is_dir())
        record = self.manifest_record()
        self.assertEqual(record["plist"], f"~/Library/LaunchAgents/{LABEL}.plist")
        self.assertEqual((record["weekday"], record["hour"], record["minute"]), (5, 17, 45))
        self.assertEqual(self.plist.stat().st_mode & 0o777, 0o644)

    def test_reinstall_boots_out_then_bootstraps(self) -> None:
        self.assertEqual(self.run_schedule("install", "--apply")[0], 0)
        self.launchctl.calls.clear()
        code, output = self.run_schedule("install", "--hour", "9", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(
            self.launchctl.calls,
            [["bootout", f"gui/{UID}/{LABEL}"], ["bootstrap", f"gui/{UID}", str(self.plist)]],
        )
        self.assertEqual(plistlib.loads(self.plist.read_bytes())["StartCalendarInterval"]["Hour"], 9)
        self.assertEqual(self.manifest_record()["hour"], 9)

    def test_refuses_to_overwrite_unmanaged_plist(self) -> None:
        write(self.plist, "owner's own job\n")
        with self.assertRaises(PakemError) as raised:
            self.run_schedule("install", "--apply")
        self.assertIn("was not installed by pakem", str(raised.exception))
        self.assertEqual(self.plist.read_text(), "owner's own job\n")
        self.assertEqual(self.launchctl.calls, [])

    def test_failed_bootstrap_removes_new_plist_and_records_nothing(self) -> None:
        launchctl = FakeLaunchctl(returncodes={"bootstrap": 5})
        code, output = self.run_schedule("install", "--apply", context=self.context(runner=launchctl))
        self.assertEqual(code, 1)
        self.assertIn("ERROR: launchctl bootstrap exited 5", output)
        self.assertFalse(self.plist.exists())
        self.assertIsNone(self.manifest_record())

    def test_uninstall_boots_out_and_removes_only_its_plist(self) -> None:
        other = write(self.plist.parent / "com.example.other.plist", "other job\n")
        self.assertEqual(self.run_schedule("install", "--apply")[0], 0)
        self.launchctl.calls.clear()

        code, output = self.run_schedule("uninstall")
        self.assertEqual(code, 0)
        self.assertIn(f"run: launchctl bootout gui/{UID}/{LABEL}", output)
        self.assertTrue(self.plist.exists())
        self.assertEqual(self.launchctl.calls, [])

        code, output = self.run_schedule("uninstall", "--apply")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.launchctl.calls, [["bootout", f"gui/{UID}/{LABEL}"]])
        self.assertFalse(self.plist.exists())
        self.assertIsNone(self.manifest_record())
        self.assertEqual(other.read_text(), "other job\n")

    def test_uninstall_tolerates_job_that_is_not_loaded(self) -> None:
        self.assertEqual(self.run_schedule("install", "--apply")[0], 0)
        launchctl = FakeLaunchctl(returncodes={"bootout": 3})
        code, output = self.run_schedule("uninstall", "--apply", context=self.context(runner=launchctl))
        self.assertEqual(code, 0, output)
        self.assertIn("note: bootout exited 3", output)
        self.assertFalse(self.plist.exists())

    def test_uninstall_keeps_edited_plist(self) -> None:
        self.assertEqual(self.run_schedule("install", "--apply")[0], 0)
        self.launchctl.calls.clear()
        write(self.plist, self.plist.read_text() + "<!-- edited -->\n")
        code, output = self.run_schedule("uninstall", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("was edited after install", output)
        self.assertTrue(self.plist.exists())
        self.assertIsNotNone(self.manifest_record())
        self.assertEqual(self.launchctl.calls, [])

    def test_uninstall_leaves_unrecorded_plist(self) -> None:
        write(self.plist, "owner's own job\n")
        code, output = self.run_schedule("uninstall", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("was not installed by pakem; left in place", output)
        self.assertTrue(self.plist.exists())
        self.assertEqual(self.launchctl.calls, [])

    def test_status_summarizes_loaded_job(self) -> None:
        self.assertEqual(self.run_schedule("install", "--apply")[0], 0)
        launchctl = FakeLaunchctl(stdout=SAMPLE_PRINT)
        code, output = self.run_schedule("status", context=self.context(runner=launchctl))
        self.assertEqual(code, 0)
        self.assertEqual(launchctl.calls, [["print", f"gui/{UID}/{LABEL}"]])
        self.assertIn("(installed by pakem)", output)
        self.assertIn("schedule: weekday 1 at 08:00", output)
        self.assertIn("launchd: loaded", output)
        self.assertIn("state = not running", output)
        self.assertNotIn("state = active", output)
        self.assertIn("last exit code = 0", output)

    def test_status_tolerates_absence(self) -> None:
        launchctl = FakeLaunchctl(returncodes={"print": 113})
        code, output = self.run_schedule("status", context=self.context(runner=launchctl))
        self.assertEqual(code, 0)
        self.assertIn("(absent)", output)
        self.assertIn("not loaded (launchctl print exited 113)", output)

        code, output = self.run_schedule("status", context=self.context(runner=missing_launchctl))
        self.assertEqual(code, 0)
        self.assertIn("launchctl not found", output)

    def test_refuses_on_other_platforms(self) -> None:
        for arguments in (["install", "--apply"], ["uninstall", "--apply"], ["status"]):
            with self.subTest(arguments=arguments):
                with self.assertRaises(PakemError) as raised:
                    self.run_schedule(*arguments, context=self.context(platform="linux"))
                self.assertIn("macOS (launchd) only; this platform is linux", str(raised.exception))
        self.assertEqual(self.launchctl.calls, [])
        self.assertFalse((self.home / "Library").exists())
        self.assertFalse(self.manifest_path.exists())


class ScheduleCommandLineTests(IsolatedTestCase):
    def test_rejects_out_of_range_times(self) -> None:
        for arguments in (["--hour", "24"], ["--minute", "60"], ["--weekday", "8"], ["--hour", "x"]):
            with self.subTest(arguments=arguments):
                completed = self.pakem("schedule", "install", *arguments)
                self.assert_fails(completed, code=2)
                self.assertIn("argument", completed.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "pakem schedule runs on macOS only")
    def test_dry_run_through_cli_writes_nothing(self) -> None:
        completed = self.pakem("schedule", "install")
        self.assert_ok(completed)
        self.assertIn("<string>com.pakem.drift-report</string>", completed.stdout)
        self.assertIn("launchctl bootstrap gui/", completed.stdout)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
