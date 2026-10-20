#!/usr/bin/env python3
"""Release engine: the only program that changes a running environment (contracts v1, §5).

    af_engine.py deploy --config /etc/af/hosts/<project>-<environment>.toml
    af_engine.py status --config ...
    af_engine.py secret-set --config ... NAME        (value on standard input)

Host configuration (TOML, private):

    project = "example"
    environment = "production"
    base = "/srv/deploy/example/production"     # private directory, owned by this user
    pointer = "ghcr.io/owner/example:release-production"
    public_key = "/etc/af/keys/example.pub"     # the project's release signing key
    registry_credentials = "/etc/af/registries.json"   # optional, read-only tokens
    lock_wait_seconds = 0
    notify = "/etc/af/notify.toml"              # optional channels file (af_notify.py)

Standard library plus `docker` (with Compose) and `cosign`. Output names steps, services,
and settings, never values.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import af_notify
import af_oci
import af_release
import af_settings
import contracts

ENGINE_VERSION = "1.0.0-draft"
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_RESTORED, EXIT_RESTORE_FAILED, EXIT_STOPPED = 0, 1, 2, 3, 4, 5
EXIT_BUSY = 75
HOOK_TIMEOUT = 1800
COMMAND_TIMEOUT = 3600
HEALTH_ATTEMPTS = 60
HEALTH_INTERVAL = 2.0


class EngineError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def log(message: str) -> None:
    print(f"af-engine: {message}", flush=True)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class HostConfig:
    project: str
    environment: str
    base: Path
    pointer: str
    public_key: Path
    registry_credentials: Path | None = None
    lock_wait_seconds: int = 0
    notify: Path | None = None

    @classmethod
    def load(cls, path: Path) -> HostConfig:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        allowed = {
            "project", "environment", "base", "pointer", "public_key", "registry_credentials", "lock_wait_seconds",
            "notify",
        }
        unknown = set(data) - allowed
        if unknown:
            raise EngineError(f"host config has unknown keys: {', '.join(sorted(unknown))}", EXIT_REFUSED)
        try:
            config = cls(
                project=data["project"],
                environment=data["environment"],
                base=Path(data["base"]),
                pointer=data["pointer"],
                public_key=Path(data["public_key"]),
                registry_credentials=Path(data["registry_credentials"]) if data.get("registry_credentials") else None,
                lock_wait_seconds=int(data.get("lock_wait_seconds", 0)),
                notify=Path(data["notify"]) if data.get("notify") else None,
            )
        except (KeyError, TypeError, ValueError):
            raise EngineError("host config needs project, environment, base, pointer, public_key", EXIT_REFUSED) from None
        if not contracts.PROJECT.fullmatch(config.project) or not contracts.ENVIRONMENT.fullmatch(config.environment):
            raise EngineError("host config has an invalid project or environment name", EXIT_REFUSED)
        if not config.base.is_absolute() or (config.notify is not None and not config.notify.is_absolute()):
            raise EngineError("host config base and notify must be absolute paths", EXIT_REFUSED)
        return config

    @property
    def compose_project(self) -> str:
        return f"{self.project}-{self.environment}"


def check_private_directory(path: Path, label: str) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EngineError(f"{label} must be a real directory", EXIT_REFUSED)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise EngineError(f"{label} must be owned by this user and not writable by others", EXIT_REFUSED)


Runner = Callable[[list[str], dict[str, str], Path | None, int], int]
HealthProbe = Callable[[str], dict | None]


def run_command(arguments: list[str], env: dict[str, str], cwd: Path | None, timeout: int) -> int:
    """Run a fixed argument list; its output goes to the log, its environment is minimal."""
    try:
        return subprocess.run(arguments, env=env, cwd=cwd, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        log(f"{arguments[0]} {arguments[1] if len(arguments) > 1 else ''} timed out")
        return 124


def probe_health(url: str) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=3) as response:
            body = json.loads(response.read(65536))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


@dataclass
class Release:
    """One unpacked release on this host."""

    directory: Path
    record: dict
    config: dict = field(default_factory=dict)

    @property
    def bundle(self) -> Path:
        return self.directory / "bundle"

    @property
    def runtime(self) -> Path:
        return self.directory / "runtime.env"


class Engine:
    def __init__(
        self,
        host: HostConfig,
        *,
        registry: af_oci.Registry | None = None,
        verify: af_release.Verify | None = None,
        runner: Runner = run_command,
        health: HealthProbe = probe_health,
        sleep: Callable[[float], None] = time.sleep,
        health_attempts: int = HEALTH_ATTEMPTS,
        trigger: str = "pull",
        actor: str = "agent",
        placement_values: dict[str, str] | None = None,
        notifier: af_notify.Notifier | None = None,
    ) -> None:
        self.host = host
        credentials = (
            af_oci.credentials_file(host.registry_credentials) if host.registry_credentials else af_oci.no_credentials
        )
        self.registry = registry or af_oci.Registry(credentials)
        self.verify = verify or af_release.CosignVerifier(host.public_key)
        self.runner = runner
        self.health = health
        self.sleep = sleep
        self.health_attempts = health_attempts
        self.trigger = trigger
        self.actor = actor
        self.placement_values = placement_values
        self.notifier = notifier or af_notify.from_file(host.notify, log)
        self.base = host.base
        self.releases = self.base / "releases"
        self.secrets = self.base / "secrets"

    # -- environment and commands ------------------------------------------------------

    def _env(self, release: Release) -> dict[str, str]:
        env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if key in os.environ}
        for name, reference in release.record["images"].items():
            env[f"AF_IMAGE_{name.upper().replace('-', '_')}"] = reference
        env.update(
            AF_PROJECT=self.host.project,
            AF_ENVIRONMENT=self.host.environment,
            AF_COMMIT=release.record["commit"],
            AF_VERSION=release.record["version"] or "",
            AF_BASE=str(self.base),
            AF_RELEASE_DIR=str(release.directory),
            AF_RUNTIME_FILE=str(release.runtime),
            AF_SECRETS_DIR=str(self.secrets),
            AF_COMPOSE_PROJECT=self.host.compose_project,
        )
        return env

    def compose(self, release: Release, *arguments: str, timeout: int = COMMAND_TIMEOUT) -> int:
        command = [
            "docker", "compose",
            "--project-name", self.host.compose_project,
            "--project-directory", str(release.bundle),
            "--env-file", str(release.runtime),
        ]
        for name in release.config["compose_files"]:
            command += ["--file", str(release.bundle / name)]
        return self.runner([*command, *arguments], self._env(release), release.bundle, timeout)

    def hook(self, release: Release, name: str, extra: dict[str, str] | None = None) -> bool:
        """Run deploy/hooks/<name> if present; True when absent or successful."""
        path = release.bundle / "deploy" / "hooks" / name
        if not path.exists():
            return True
        log(f"hook {name}")
        env = {**self._env(release), **(extra or {})}
        return self.runner([str(path)], env, release.bundle, HOOK_TIMEOUT) == 0

    def healthy(self, release: Release) -> bool:
        health = release.config["health"]
        port = read_runtime(release.runtime).get(health["port_setting"], "")
        if not port.isdigit():
            return False
        url = f"http://127.0.0.1:{port}{health['path']}"
        for attempt in range(self.health_attempts):
            body = self.health(url)
            if body is not None and body.get(health["revision_key"]) == release.record["commit"]:
                return True
            if attempt + 1 < self.health_attempts:
                self.sleep(HEALTH_INTERVAL)
        return False

    # -- releases on disk --------------------------------------------------------------

    def current(self) -> Release | None:
        link = self.base / "current"
        if not link.is_symlink():
            return None
        target = os.readlink(link)
        if not re.fullmatch(r"releases/[0-9a-f]{40}", target):
            raise EngineError("current points somewhere unexpected", EXIT_REFUSED)
        directory = self.base / target
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        config = tomllib.loads((directory / "bundle/deploy/release.toml").read_text(encoding="utf-8"))
        return Release(directory, record, config)

    def prepare(self, release: af_release.Release, current: Release | None) -> Release:
        directory = self.releases / release.record["commit"]
        if current is not None and current.directory == directory:
            # Same commit, new record (a re-promotion or rollback record): the running
            # release's files stay in place; only an identical bundle may be reused.
            if current.record["bundle"] != release.record["bundle"]:
                raise EngineError("the running commit was republished with a different bundle", EXIT_REFUSED)
            write_json(directory / "record.json", release.record)
            return Release(directory, release.record, current.config)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(mode=0o700, parents=True)
        config = af_release.fetch_bundle(self.registry, release.record, directory / "bundle")
        write_json(directory / "record.json", release.record)
        return Release(directory, release.record, config)

    def promote(self, release: Release) -> None:
        temporary = self.base / f".current-{os.getpid()}"
        temporary.unlink(missing_ok=True)
        os.symlink(f"releases/{release.record['commit']}", temporary)
        os.replace(temporary, self.base / "current")

    def retain(self, keep: int, protect: set[str]) -> None:
        """Keep the newest releases plus the running and previous ones; remove the rest
        and their images (an image still used by a kept release is never removed)."""
        directories = sorted(
            (path for path in self.releases.iterdir() if path.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True
        )
        kept = [d for d in directories[:keep]] + [d for d in directories[keep:] if d.name in protect]
        in_use = set()
        for directory in kept:
            record_path = directory / "record.json"
            if record_path.exists():
                in_use.update(json.loads(record_path.read_text(encoding="utf-8"))["images"].values())
        env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST") if key in os.environ}
        for directory in directories[keep:]:
            if directory.name in protect:
                continue
            record_path = directory / "record.json"
            images = json.loads(record_path.read_text(encoding="utf-8"))["images"].values() if record_path.exists() else []
            for image in images:
                if image not in in_use:
                    self.runner(["docker", "image", "rm", image], env, None, COMMAND_TIMEOUT)
            shutil.rmtree(directory)

    # -- the sequence ------------------------------------------------------------------

    def deploy(self) -> dict:
        check_private_directory(self.base, "base")
        self.releases.mkdir(mode=0o700, exist_ok=True)
        if not self.secrets.exists():
            self.secrets.mkdir(mode=0o700)
        started = utc_now()
        lock = self._lock()
        try:
            return self._deploy(started)
        finally:
            os.close(lock)

    def _lock(self) -> int:
        descriptor = os.open(self.base / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.host.lock_wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise EngineError("another deployment holds the lock", EXIT_BUSY) from None
                self.sleep(1.0)

    def _deploy(self, started: str) -> dict:
        previous = self.current()
        try:
            fetched = af_release.fetch_release(self.registry, self.host.pointer, self.host.project, self.verify)
            order = af_release.check_order(previous.record if previous else None, fetched.record)
        except af_release.ReleaseError as error:
            raise EngineError(str(error), EXIT_REFUSED) from None
        if order == "unchanged":
            log("this release already runs; nothing to do")
            return self._record("unchanged", EXIT_OK, fetched, previous, started, backup=None)
        log(f"deploying {fetched.record['commit'][:12]} ({fetched.record['version'] or 'unversioned'})")
        try:
            candidate = self.prepare(fetched, previous)
            if candidate.config.get("switch", "stop-start") != "stop-start":
                raise EngineError("blue-green switching needs the host proxy (not in this engine version)", EXIT_REFUSED)
            settings = af_settings.build_runtime(
                candidate.bundle,
                self.host.environment,
                self.secrets,
                candidate.runtime,
                placement_file=None if self.placement_values is not None else self.base / "placement.env",
                placement_values=self.placement_values,
            )
        except (af_release.ReleaseError, af_settings.SettingsError) as error:
            raise EngineError(str(error), EXIT_REFUSED) from None
        if settings.generated:
            log(f"generated secrets: {', '.join(settings.generated)}")

        # Nothing has changed yet: failures here leave the running release untouched.
        for reference in candidate.record["images"].values():
            if self.runner(["docker", "pull", "--quiet", reference], self._env(candidate), None, COMMAND_TIMEOUT) != 0:
                return self._fail_early("image pull failed", fetched, previous, started)
        if self.compose(candidate, "config", "--quiet") != 0:
            return self._fail_early("compose configuration is invalid", fetched, previous, started)
        preflight = candidate.config.get("preflight") or []
        if preflight:
            service = candidate.config.get("preflight_service", candidate.config["health"]["service"])
            if self.compose(candidate, "run", "--rm", "--no-deps", service, *preflight) != 0:
                return self._fail_early("preflight refused the configuration", fetched, previous, started)
        if not self.hook(candidate, "pre_migrate"):
            return self._fail_early("pre_migrate hook failed", fetched, previous, started)

        # From here the old release is stopped; failures restore it or stop for the owner.
        if previous is not None:
            log("stopping the previous release")
            drain = str(int(candidate.config.get("drain_seconds", 60)))
            self.compose(previous, "stop", "--timeout", drain)
        backup = None
        migrations = bool(candidate.config.get("migrate"))
        environment = candidate.config["environments"].get(self.host.environment, {})
        backup_required = migrations and environment.get("backup", "optional") == "required"
        hook_present = (candidate.bundle / "deploy/hooks/backup").exists()
        if backup_required and not hook_present:
            return self._fail_late("backup is required but the bundle has no backup hook", candidate, fetched, previous, started, None)
        if hook_present and previous is not None:
            backup_dir = self.base / "backups" / started.replace(":", "").replace("-", "")
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.hook(candidate, "backup", {"AF_BACKUP_DIR": str(backup_dir)}) or not any(backup_dir.iterdir()):
                return self._fail_late("backup hook failed or wrote nothing", candidate, fetched, previous, started, None)
            backup = str(backup_dir)
        if migrations:
            service = candidate.config.get("migrate_service", candidate.config["health"]["service"])
            if self.compose(candidate, "run", "--rm", "--no-deps", service, *candidate.config["migrate"]) != 0:
                return self._fail_late("migration failed", candidate, fetched, previous, started, backup)
        if self.compose(candidate, "up", "--detach", "--no-build", "--remove-orphans") != 0:
            return self._fail_late("start failed", candidate, fetched, previous, started, backup)
        if not self.healthy(candidate):
            return self._fail_late("health check did not report this release", candidate, fetched, previous, started, backup)
        if not self.hook(candidate, "post_switch") or not self.hook(candidate, "smoke"):
            return self._fail_late("post-switch or smoke hook failed", candidate, fetched, previous, started, backup)

        self.promote(candidate)
        keep = int(environment.get("keep", 3))
        protect = {candidate.record["commit"]} | ({previous.record["commit"]} if previous else set())
        self.retain(keep, protect)
        log("deployed")
        return self._record("deployed", EXIT_OK, fetched, previous, started, backup)

    def _fail_early(self, reason: str, fetched: af_release.Release, previous: Release | None, started: str) -> dict:
        log(f"{reason}; the running release was not touched")
        return self._record("failed", EXIT_FAILED, fetched, previous, started, None, reason)

    def _fail_late(
        self,
        reason: str,
        candidate: Release,
        fetched: af_release.Release,
        previous: Release | None,
        started: str,
        backup: str | None,
    ) -> dict:
        log(reason)
        self.compose(candidate, "stop")
        if previous is None:
            log("first deployment failed; services stay stopped")
            return self._record("failed", EXIT_FAILED, fetched, previous, started, backup, reason)
        if candidate.record["schema_version"] != previous.record["schema_version"]:
            log("the schema changed; services stay stopped for the owner (restore from the backup)")
            return self._record("stopped", EXIT_STOPPED, fetched, previous, started, backup, reason)
        log("restoring the previous release")
        if self.compose(previous, "up", "--detach", "--no-build", "--remove-orphans") == 0 and self.healthy(previous):
            log("previous release restored")
            return self._record("restored", EXIT_RESTORED, fetched, previous, started, backup, reason)
        log("restore failed; act manually")
        return self._record("restore_failed", EXIT_RESTORE_FAILED, fetched, previous, started, backup, reason)

    def _record(
        self,
        result: str,
        exit_code: int,
        fetched: af_release.Release,
        previous: Release | None,
        started: str,
        backup: str | None,
        reason: str | None = None,
    ) -> dict:
        record = {
            "schema": contracts.DEPLOYMENT_SCHEMA,
            "project": self.host.project,
            "environment": self.host.environment,
            "result": result,
            "exit_code": exit_code,
            "release": fetched.digest,
            "commit": fetched.record["commit"],
            "version": fetched.record["version"],
            "images": fetched.record["images"],
            "schema_version": fetched.record["schema_version"],
            "previous_commit": previous.record["commit"] if previous else None,
            "previous_schema_version": previous.record["schema_version"] if previous else None,
            "backup": backup,
            "trigger": self.trigger,
            "actor": self.actor,
            "started": started,
            "finished": utc_now(),
            "engine": ENGINE_VERSION,
        }
        problems = contracts.validate_deployment_record(record)
        if problems:
            raise EngineError("; ".join(problems), EXIT_FAILED)
        write_json(self.base / "deployment.json", record)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.base / "deployments.jsonl", flags, 0o600), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self.notifier.send(result, af_notify.deployment_text(record, reason), record)
        return record


def read_runtime(path: Path) -> dict[str, str]:
    """Read the NAME='value' lines af_settings writes (values never contain a quote)."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)='([^']*)'", line)
        if match:
            values[match[1]] = match[2]
    return values


def read_json(path: Path) -> dict:
    """A JSON object from a host state file, or {} when the file does not exist."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(temporary, flags, 0o600), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_engine.py", description="Release engine (contracts v1)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("deploy", "status", "secret-set"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, required=True)
        if name == "secret-set":
            sub.add_argument("name")
        if name == "deploy":
            sub.add_argument("--trigger", choices=("pull", "push"), default="pull")
            sub.add_argument("--actor", default="agent")
    args = parser.parse_args(argv)
    try:
        host = HostConfig.load(args.config)
        if args.command == "status":
            path = host.base / "deployment.json"
            print(path.read_text(encoding="utf-8") if path.exists() else "{}")
            return EXIT_OK
        if args.command == "secret-set":
            af_settings.set_secret(host.base / "secrets", args.name, sys.stdin.buffer.read().rstrip(b"\n"))
            log(f"secret {args.name} stored")
            return EXIT_OK
        record = Engine(host, trigger=args.trigger, actor=args.actor).deploy()
        return int(record["exit_code"])
    except EngineError as error:
        log(f"refused: {error}" if error.exit_code == EXIT_REFUSED else str(error))
        return error.exit_code
    except af_settings.SettingsError as error:
        log(f"refused: {error}")
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
