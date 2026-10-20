#!/usr/bin/env python3
"""Release triggers (ADR 0009): how the engine learns that a release pointer moved.

    af_trigger.py poll --config-dir /etc/af/hosts
        Pull agent, run by the af-agent systemd timer. For each host config it resolves the
        environment's release pointer and runs the engine when the pointer names a release
        this host has not attempted yet. CI holds no host credentials.

    af_trigger.py receive --config-dir /etc/af/hosts --project example [--environment staging]
        Push receiver: the forced command of a project's CI key in authorized_keys,

            restrict,command="/usr/bin/python3 /opt/af/af_trigger.py receive --config-dir /etc/af/hosts --project example" ssh-ed25519 AAAA... example-ci

        SSH_ORIGINAL_COMMAND may only be `deploy <environment> [<actor>]` or
        `status <environment>`. CI never sends release data; the engine reads the signed
        pointer itself, so the key can only ask for what is already published.

Both triggers run the same engine with the same host config, so a release deploys the
same way whichever trigger noticed it. Output names hosts and steps, never values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import af_engine
import af_oci
import af_settings
import contracts

# A release the engine refused (for example a missing secret) is retried after this long,
# so fixing the host is enough; a failed or restored release is never retried by the agent.
REFUSED_RETRY_SECONDS = 900
AGENT_STATE = "agent.json"
ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\[\]-]{0,63}")
MAX_COMMAND = 200

EngineFactory = Callable[..., af_engine.Engine]


def log(message: str) -> None:
    print(f"af-trigger: {message}", flush=True)


def registry_for(host: af_engine.HostConfig) -> af_oci.Registry:
    if host.registry_credentials is None:
        return af_oci.Registry(af_oci.no_credentials)
    return af_oci.Registry(af_oci.credentials_file(host.registry_credentials))


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


class Agent:
    def __init__(
        self,
        *,
        engine: EngineFactory = af_engine.Engine,
        registry: Callable[[af_engine.HostConfig], af_oci.Registry] = registry_for,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.clock = clock

    def poll(self, directory: Path) -> int:
        """One pass over every host config; 1 when any host needs attention."""
        status = af_engine.EXIT_OK
        for path in sorted(directory.glob("*.toml")):
            try:
                host = af_engine.HostConfig.load(path)
            except (af_engine.EngineError, OSError, ValueError) as error:
                log(f"{path.name}: unusable host config: {error}")
                status = af_engine.EXIT_FAILED
                continue
            try:
                result = self.check(host)
            except (OSError, ValueError) as error:
                log(f"{host.compose_project}: host state unreadable: {error}")
                result = af_engine.EXIT_FAILED
            if result != af_engine.EXIT_OK:
                status = af_engine.EXIT_FAILED
        return status

    def check(self, host: af_engine.HostConfig) -> int:
        label = host.compose_project
        try:
            registry = self.registry(host)
            digest = registry.resolve(af_oci.Reference.parse(host.pointer))
        except af_oci.RegistryError as error:
            log(f"{label}: release pointer unavailable: {error}")
            return af_engine.EXIT_FAILED
        if digest == read_json(host.base / "deployment.json").get("release"):
            return af_engine.EXIT_OK
        state_path = host.base / AGENT_STATE
        state = read_json(state_path)
        if state.get("refused") == digest and self.clock() - state.get("refused_at", 0) < REFUSED_RETRY_SECONDS:
            return af_engine.EXIT_FAILED  # still refused; logged when it happened
        log(f"{label}: release pointer moved to {digest}; running the engine")
        try:
            record = self.engine(host, registry=registry, trigger="pull", actor="agent").deploy()
        except (af_engine.EngineError, af_settings.SettingsError) as error:
            if getattr(error, "exit_code", None) == af_engine.EXIT_BUSY:
                log(f"{label}: another deployment holds the lock; next poll retries")
                return af_engine.EXIT_OK
            log(f"{label}: refused: {error}")
            af_engine.write_json(state_path, {"refused": digest, "refused_at": self.clock()})
            return af_engine.EXIT_FAILED
        state_path.unlink(missing_ok=True)
        log(f"{label}: {record['result']} {record['commit'][:12]}")
        return af_engine.EXIT_OK if record["exit_code"] == af_engine.EXIT_OK else af_engine.EXIT_FAILED


def receive(
    directory: Path,
    project: str,
    environments: list[str],
    command: str | None,
    *,
    engine: EngineFactory = af_engine.Engine,
) -> int:
    """Run one forced-command request; the reply is the engine's exit code."""
    usage = "expected `deploy <environment> [<actor>]` or `status <environment>`"
    if not command or len(command) > MAX_COMMAND or not command.isascii():
        log(f"refused: {usage}")
        return af_engine.EXIT_REFUSED
    words = command.split()
    if not (
        (len(words) in (2, 3) and words[0] == "deploy") or (len(words) == 2 and words[0] == "status")
    ):
        log(f"refused: {usage}")
        return af_engine.EXIT_REFUSED
    action, environment = words[0], words[1]
    actor = words[2] if len(words) == 3 else "ci"
    if not contracts.ENVIRONMENT.fullmatch(environment) or not ACTOR.fullmatch(actor):
        log("refused: invalid environment or actor name")
        return af_engine.EXIT_REFUSED
    if environments and environment not in environments:
        log(f"refused: this key may not trigger {environment}")
        return af_engine.EXIT_REFUSED
    path = directory / f"{project}-{environment}.toml"
    try:
        if not path.is_file():
            raise af_engine.EngineError(f"no host config for {project} {environment}", af_engine.EXIT_REFUSED)
        host = af_engine.HostConfig.load(path)
        if (host.project, host.environment) != (project, environment):
            raise af_engine.EngineError(f"{path.name} names another project or environment", af_engine.EXIT_REFUSED)
        if action == "status":
            print(json.dumps(read_json(host.base / "deployment.json"), indent=2, sort_keys=True))
            return af_engine.EXIT_OK
        record = engine(host, trigger="push", actor=actor).deploy()
        return int(record["exit_code"])
    except af_engine.EngineError as error:
        log(f"refused: {error}" if error.exit_code == af_engine.EXIT_REFUSED else str(error))
        return error.exit_code
    except af_settings.SettingsError as error:
        log(f"refused: {error}")
        return af_engine.EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_trigger.py", description="Release triggers (contracts v1)")
    commands = parser.add_subparsers(dest="command", required=True)
    poll = commands.add_parser("poll", help="pull agent: one pass over every host config")
    poll.add_argument("--config-dir", type=Path, required=True)
    push = commands.add_parser("receive", help="push receiver: the forced command of a CI key")
    push.add_argument("--config-dir", type=Path, required=True)
    push.add_argument("--project", required=True)
    push.add_argument("--environment", action="append", default=[], help="allowed environment (repeatable)")
    args = parser.parse_args(argv)
    if args.command == "poll":
        return Agent().poll(args.config_dir)
    if not contracts.PROJECT.fullmatch(args.project):
        log("refused: invalid project name")
        return af_engine.EXIT_REFUSED
    return receive(args.config_dir, args.project, args.environment, os.environ.get("SSH_ORIGINAL_COMMAND"))


if __name__ == "__main__":
    sys.exit(main())
