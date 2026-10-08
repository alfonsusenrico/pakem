"""`af schedule install|uninstall|status`: run `af report` weekly through a macOS launchd agent.

The plist is generated, never tracked, and recorded in the manifest with its sha256 so
uninstall removes only a plist that af wrote and nobody edited since. launchctl is
reached through an injectable runner so tests never load a real job.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .common import (
    AfError,
    Framework,
    display_path,
    expand_user_path,
    home_dir,
    sha256_file,
    utc_now_iso,
    write_text_atomically,
)
from .manifest import load_manifest, reports_dir, save_manifest
from .source import framework_source_status

LABEL = "com.agentframework.drift-report"
SUPPORTED_PLATFORM = "darwin"
DEFAULT_WEEKDAY = 1  # launchd: 0 and 7 are Sunday, 1 is Monday.
DEFAULT_HOUR = 8
DEFAULT_MINUTE = 0
LOG_NAME = "launchd.log"
PLIST_MODE = 0o644
STATUS_KEYS = ("state =", "last exit code =", "runs =", "path =")

LaunchctlRunner = Callable[[list[str]], subprocess.CompletedProcess]


def run_launchctl(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *arguments], capture_output=True, text=True)


@dataclass(frozen=True)
class ScheduleContext:
    """What differs between a real run and a test: platform, user id, launchctl, interpreter."""

    platform: str
    uid: int
    runner: LaunchctlRunner
    python: str


def default_context() -> ScheduleContext:
    return ScheduleContext(
        platform=sys.platform, uid=os.getuid(), runner=run_launchctl, python=sys.executable
    )


def plist_path() -> Path:
    return home_dir() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return reports_dir() / LOG_NAME


def render_plist(framework: Framework, python: str, weekday: int, hour: int, minute: int) -> str:
    # launchd does not expand ~, so every path in the plist is absolute.
    job = {
        "Label": LABEL,
        "ProgramArguments": [python, str(framework.root / "tools" / "af"), "report"],
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
        "RunAtLoad": False,
    }
    return plistlib.dumps(job, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")


def bootstrap_command(uid: int, path: Path) -> list[str]:
    return ["bootstrap", f"gui/{uid}", str(path)]


def bootout_command(uid: int) -> list[str]:
    return ["bootout", f"gui/{uid}/{LABEL}"]


def print_command(uid: int) -> list[str]:
    return ["print", f"gui/{uid}/{LABEL}"]


def _shell_line(arguments: list[str]) -> str:
    return "launchctl " + shlex.join(arguments)


def _require_platform(context: ScheduleContext) -> None:
    if context.platform != SUPPORTED_PLATFORM:
        raise AfError(
            f"af schedule supports macOS (launchd) only; this platform is {context.platform}. "
            "Run `af report` from this system's own scheduler instead."
        )


def _bootout(context: ScheduleContext) -> None:
    """Unload the job; a job that is not loaded makes bootout fail, which is reported, not fatal."""
    completed = context.runner(bootout_command(context.uid))
    if completed.returncode != 0:
        print(f"  note: bootout exited {completed.returncode} (job not loaded?): {completed.stderr.strip()}")


def _managed(record: dict | None, path: Path) -> bool:
    """True when path is the regular file af recorded, unchanged since."""
    if record is None or not path.is_file() or path.is_symlink():
        return False
    return sha256_file(path) == record.get("sha256")


def install(framework: Framework, args, context: ScheduleContext) -> int:
    manifest = load_manifest()
    record = manifest["schedule"].get(LABEL)
    path = plist_path()
    text = render_plist(framework, context.python, args.weekday, args.hour, args.minute)
    if (path.exists() or path.is_symlink()) and not _managed(record, path):
        raise AfError(
            f"{display_path(path)} exists and was not installed by af, or was edited since; "
            "refusing to overwrite (remove it by hand if intended)"
        )

    commands = []
    if record is not None:
        commands.append(bootout_command(context.uid))
    commands.append(bootstrap_command(context.uid, path))

    print(f"af schedule install ({'apply' if args.apply else 'dry-run'})")
    source = framework_source_status(framework.root)
    print(f"  framework source: {source.summary}")
    for finding in source.findings:
        print(f"WARNING: {finding.message}; the job runs tools/af from this checkout")
    print(f"  plist: {display_path(path)}")
    print(text, end="")
    for command in commands:
        print(f"  run: {_shell_line(command)}")
    if not args.apply:
        print("Dry run: nothing written and launchctl not run. Re-run with --apply to install.")
        return 0

    previous_text = path.read_text(encoding="utf-8") if path.exists() else None
    if record is not None:
        _bootout(context)
    reports_dir().mkdir(parents=True, exist_ok=True)
    write_text_atomically(path, text, mode=PLIST_MODE)
    completed = context.runner(bootstrap_command(context.uid, path))
    if completed.returncode != 0:
        if previous_text is None:
            path.unlink()
            print(f"  removed {display_path(path)} (bootstrap failed)")
        else:
            write_text_atomically(path, previous_text, mode=PLIST_MODE)
            print(f"  restored the previous {display_path(path)}; it is no longer loaded")
        print(f"ERROR: launchctl bootstrap exited {completed.returncode}: {completed.stderr.strip()}")
        return 1

    manifest["schedule"][LABEL] = {
        "plist": display_path(path),
        "sha256": sha256_file(path),
        "weekday": args.weekday,
        "hour": args.hour,
        "minute": args.minute,
        "installed_at": utc_now_iso(),
    }
    save_manifest(manifest)
    print("Installed.")
    return 0


def uninstall(args, context: ScheduleContext) -> int:
    manifest = load_manifest()
    record = manifest["schedule"].get(LABEL)
    print(f"af schedule uninstall ({'apply' if args.apply else 'dry-run'})")
    if record is None:
        path = plist_path()
        if path.exists() or path.is_symlink():
            print(f"  {display_path(path)} exists but was not installed by af; left in place")
        else:
            print("  nothing recorded for the drift report schedule")
        return 0

    path = expand_user_path(record["plist"])
    if path.exists() and not _managed(record, path):
        print(f"ERROR: {record['plist']} was edited after install; unload and remove it by hand if intended")
        return 1
    command = bootout_command(context.uid)
    print(f"  run: {_shell_line(command)}")
    print(f"  remove {record['plist']}")
    if not args.apply:
        print("Dry run: nothing removed and launchctl not run. Re-run with --apply to uninstall.")
        return 0

    _bootout(context)
    if path.exists():
        path.unlink()
    del manifest["schedule"][LABEL]
    save_manifest(manifest)
    print("Uninstalled.")
    return 0


def status(context: ScheduleContext) -> int:
    record = load_manifest()["schedule"].get(LABEL)
    path = plist_path()
    print("af schedule status")
    print(f"  label: {LABEL}")
    if path.exists():
        ownership = "installed by af" if _managed(record, path) else "not installed by af, or edited since"
        print(f"  plist: {display_path(path)} ({ownership})")
    else:
        print(f"  plist: {display_path(path)} (absent)")
    if record is not None:
        print(f"  schedule: weekday {record['weekday']} at {record['hour']:02d}:{record['minute']:02d}")
    print(f"  log: {display_path(log_path())}")

    try:
        completed = context.runner(print_command(context.uid))
    except FileNotFoundError:
        print("  launchd: launchctl not found")
        return 0
    if completed.returncode != 0:
        print(f"  launchd: not loaded (launchctl print exited {completed.returncode})")
        return 0
    print("  launchd: loaded")
    # launchctl print nests sub-sections that repeat some keys; the first occurrence is the job's own.
    shown: set[str] = set()
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        for key in STATUS_KEYS:
            if stripped.startswith(key) and key not in shown:
                shown.add(key)
                print(f"    {stripped}")
    return 0


def run_schedule(args, context: ScheduleContext | None = None) -> int:
    context = context or default_context()
    _require_platform(context)
    if args.schedule_command == "install":
        return install(Framework(Path(args.root)), args, context)
    if args.schedule_command == "uninstall":
        return uninstall(args, context)
    return status(context)
