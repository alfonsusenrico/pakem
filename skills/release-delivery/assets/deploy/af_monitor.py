#!/usr/bin/env python3
"""Health monitor (ADR 0009, host services): is each running release still answering?

    af_monitor.py check --config-dir /etc/af/hosts

Run by the af-monitor systemd timer. For each host config with a current release it asks
the release's health path, which must report the release's own commit. Two failed checks
in a row notify `unhealthy` once; the next passing check notifies `recovered`. An
environment that is deploying (its lock is held) is skipped. Read-only: it never starts,
stops, or restores anything.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import sys
from collections.abc import Callable
from pathlib import Path

import af_engine
import af_notify

FAILURES_BEFORE_ALERT = 2
HEALTH_ATTEMPTS = 3
STATE = "monitor.json"


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
    ) -> None:
        self.engine = engine or (
            lambda host: af_engine.Engine(host, health_attempts=HEALTH_ATTEMPTS, notifier=af_notify.Notifier([]))
        )
        self.notifier = notifier

    def check_all(self, directory: Path) -> int:
        """One pass; 1 when any environment is unhealthy or its config is unusable."""
        status = af_engine.EXIT_OK
        for path in sorted(directory.glob("*.toml")):
            try:
                host = af_engine.HostConfig.load(path)
                healthy = self.check(host)
            except (af_engine.EngineError, OSError, ValueError, KeyError) as error:
                log(f"{path.name}: cannot check: {error}")
                healthy = False
            if not healthy:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_monitor.py", description="Health monitor (contracts v1)")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="one pass over every host config")
    check.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return Monitor().check_all(args.config_dir)


if __name__ == "__main__":
    sys.exit(main())
