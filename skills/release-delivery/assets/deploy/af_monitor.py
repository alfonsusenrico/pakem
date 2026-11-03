#!/usr/bin/env python3
"""Health monitor (ADR 0009, host services): is each running release still answering, and
does the host have disk to keep going?

    af_monitor.py check --config-dir /etc/af/hosts

Run by the af-monitor systemd timer. For each host config with a current release it asks
the release's health path, which must report the release's own commit. Two failed checks
in a row notify `unhealthy` once; the next passing check notifies `recovered`. An
environment that is deploying (its lock is held) is skipped.

Each pass also measures the volumes holding Docker's data and every deploy base (ADR 0018).
Free space below the alert level, or a trend that reaches the build reserve within the
alert hours, notifies `disk_low` once; `disk_recovered` follows when both clear. The alert
level always sits above the reserve, so it fires before releases are refused for space.
Read-only: it never starts, stops, restores, or deletes anything.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import af_engine
import af_limits
import af_notify

FAILURES_BEFORE_ALERT = 2
HEALTH_ATTEMPTS = 3
STATE = "monitor.json"
DISK_STATE = "disk.json"
# One free-space sample per half hour, two days of them: enough for a trend, small on disk.
SAMPLE_SECONDS = 1800
HISTORY_SECONDS = 2 * 86400


def log(message: str) -> None:
    print(f"af-monitor: {message}", flush=True)


def deploying(base: Path) -> bool:
    """True when a deployment holds the environment's lock (checked without keeping it)."""
    path = base / "lock"
    if not path.exists():
        return False
    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


class Monitor:
    def __init__(
        self,
        *,
        engine: Callable[[af_engine.HostConfig], af_engine.Engine] | None = None,
        notifier: Callable[[af_engine.HostConfig], af_notify.Notifier] = lambda host: af_notify.from_file(host.notify, log),
        disk: DiskWatch | None = None,
    ) -> None:
        self.engine = engine or (
            lambda host: af_engine.Engine(host, health_attempts=HEALTH_ATTEMPTS, notifier=af_notify.Notifier([]))
        )
        self.notifier = notifier
        self.disk = disk or DiskWatch(notifier=lambda hosts: Fanout([self.notifier(host) for host in hosts]))

    def check_all(self, directory: Path) -> int:
        """One pass; 1 when any environment is unhealthy, its config is unusable, or disk is low."""
        status = af_engine.EXIT_OK
        hosts = []
        for path in sorted(directory.glob("*.toml")):
            try:
                host = af_engine.HostConfig.load(path)
                hosts.append(host)
                healthy = self.check(host)
            except (af_engine.EngineError, OSError, ValueError, KeyError) as error:
                log(f"{path.name}: cannot check: {error}")
                healthy = False
            if not healthy:
                status = af_engine.EXIT_FAILED
        if not self.disk.check(hosts):
            status = af_engine.EXIT_FAILED
        return status

    def check(self, host: af_engine.HostConfig) -> bool:
        engine = self.engine(host)
        current = engine.current()
        if current is None or deploying(host.base):
            return True
        state_path = host.base / STATE
        state = af_engine.read_json(state_path)
        label = f"{host.project} {host.environment}"
        commit = current.record["commit"][:12]
        if engine.healthy(current):
            if state.get("alerted"):
                self.notifier(host).send("recovered", f"{label}: recovered, {commit} answers its health check")
            state_path.unlink(missing_ok=True)
            return True
        failures = int(state.get("failures", 0)) + 1
        alerted = bool(state.get("alerted"))
        log(f"{host.compose_project}: health check failed for {commit} ({failures} in a row)")
        if failures >= FAILURES_BEFORE_ALERT and not alerted:
            self.notifier(host).send("unhealthy", f"{label}: health check failing for {commit} ({failures} checks in a row)")
            alerted = True
        af_engine.write_json(state_path, {"failures": failures, "alerted": alerted})
        return False


class Fanout:
    """One message to every channels file the host's environments name, each file once."""

    def __init__(self, notifiers: list[af_notify.Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, event: str, text: str) -> None:
        for notifier in self.notifiers:
            notifier.send(event, text)


class DiskWatch:
    """Free space on the volumes releases need, against the host's derived or set limits."""

    def __init__(
        self,
        *,
        notifier: Callable[[list[af_engine.HostConfig]], Fanout],
        docker_root: Callable[[], Path | None] | None = None,
        disk_usage=None,
        settings: Callable[[], af_limits.Settings] = af_limits.Settings.load,
        state_dir: Path = af_limits.STATE_DIR,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.notifier = notifier
        self.docker_root = docker_root or self._docker_root
        self.disk_usage = disk_usage or shutil.disk_usage
        self.settings = settings
        self.state_dir = state_dir
        self.now = now or time.time

    @staticmethod
    def _docker_root() -> Path | None:
        try:
            return af_limits.docker_root(af_limits.docker_runner("docker"))
        except (af_limits.LimitError, OSError, subprocess.SubprocessError):
            return None

    def places(self, hosts: list[af_engine.HostConfig]) -> dict[str, Path]:
        """One path per volume: Docker's data root first, then each deploy base."""
        candidates = ([("Docker data", root)] if (root := self.docker_root()) else [])
        candidates += [(f"{host.project} {host.environment}", host.base) for host in hosts]
        places: dict[str, Path] = {}
        seen = set()
        for label, path in candidates:
            try:
                device = (path if path.exists() else path.parent).stat().st_dev
            except OSError:
                device = str(path)
            if device not in seen:
                seen.add(device)
                places[label] = path
        return places

    def check(self, hosts: list[af_engine.HostConfig]) -> bool:
        try:
            settings = self.settings()
        except af_limits.LimitError as error:
            log(f"host limits unusable, using derived ones: {error}")
            settings = af_limits.Settings()
        state_path = self.state_dir / DISK_STATE
        try:
            state = af_engine.read_json(state_path)
        except (OSError, ValueError):
            state = {}
        builds = af_limits.load_builds(self.state_dir)
        hours = af_limits.alert_hours(settings).value
        now = self.now()
        ok = True
        updated: dict[str, dict] = {}
        for label, path in self.places(hosts).items():
            try:
                disk = af_limits.volume(path, self.disk_usage)
            except af_limits.LimitError as error:
                log(f"{label}: {error}")
                ok = False
                continue
            kept = af_limits.reserve(disk, builds, settings)
            try:
                alert = af_limits.alert_free(disk, int(kept.value), settings)
            except af_limits.LimitError as error:
                log(f"{error}; using the derived alert level")
                alert = af_limits.alert_free(disk, int(kept.value), af_limits.Settings())
            entry = state.get(label, {}) if isinstance(state.get(label), dict) else {}
            samples = [tuple(sample) for sample in entry.get("samples", []) if now - sample[0] <= HISTORY_SECONDS]
            if not samples or now - samples[-1][0] >= SAMPLE_SECONDS:
                samples.append((now, disk.free))
            until = af_limits.hours_until(samples, int(kept.value))
            reasons = []
            if disk.free < alert.value:
                reasons.append(f"{af_limits.format_size(disk.free)} free, below the alert level of "
                               f"{af_limits.format_size(int(alert.value))} ({alert.source})")
            if until is not None and until <= hours:
                reasons.append(f"at the current trend it reaches the build reserve of "
                               f"{af_limits.format_size(int(kept.value))} in about {until:.0f} hours")
            alerted = bool(entry.get("alerted"))
            if reasons and not alerted:
                self.notifier(hosts).send("disk_low", f"disk low for {label}: {'. '.join(reasons)}. Releases are "
                                          "refused below the reserve")
                alerted = True
            elif not reasons and alerted:
                self.notifier(hosts).send("disk_recovered", f"disk recovered for {label}: "
                                          f"{af_limits.format_size(disk.free)} free")
                alerted = False
            if reasons:
                log(f"{label}: {'. '.join(reasons)}")
                ok = False
            updated[label] = {"samples": [list(sample) for sample in samples], "alerted": alerted}
        try:
            af_engine.write_json(state_path, updated)
        except OSError as error:
            log(f"could not keep the disk history: {error.strerror or error}")
        return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_monitor.py", description="Health monitor (contracts v1)")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="one pass over every host config")
    check.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return Monitor().check_all(args.config_dir)


if __name__ == "__main__":
    sys.exit(main())
