#!/usr/bin/env python3
"""Release engine: the only program that changes a running environment (contracts v1, §5).

    af_engine.py deploy --config /etc/af/hosts/<project>-<environment>.toml
    af_engine.py status --config ...
    af_engine.py secret-set --config ... NAME        (value on standard input)

Host configuration (TOML, private):

    project = "example"
    environment = "production"
    base = "/srv/deploy/example/production"     # private directory, owned by this user
    delivery = "registry"                       # or "direct": releases arrive through af_receive.py
    pointer = "ghcr.io/owner/example:release-production"   # registry only
    public_key = "/etc/af/keys/example.pub"     # registry only: the project's release signing key
    registry_credentials = "/etc/af/registries.json"   # optional, read-only tokens
    lock_wait_seconds = 0
    notify = "/etc/af/notify.toml"              # optional channels file (af_notify.py)

    [proxy]                                     # optional: the host's reverse proxy (Caddy)
    sites = "/etc/caddy/sites"                  # af-writable directory the Caddyfile imports
    reload = ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--address", "unix//run/caddy/admin.sock"]
    tls = true                                  # false when a tunnel in front terminates TLS

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
RELOAD_TIMEOUT = 120
DOMAIN = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")


class EngineError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def log(message: str) -> None:
    print(f"af-engine: {message}", flush=True)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ProxyConfig:
    sites: Path
    reload: list[str]
    tls: bool = True

    @classmethod
    def parse(cls, data: object) -> ProxyConfig:
        if not isinstance(data, dict) or set(data) - {"sites", "reload", "tls"}:
            raise EngineError("host config [proxy] takes sites, reload, and tls", EXIT_REFUSED)
        sites, reload, tls = data.get("sites"), data.get("reload"), data.get("tls", True)
        if not isinstance(sites, str) or not Path(sites).is_absolute():
            raise EngineError("host config proxy.sites must be an absolute path", EXIT_REFUSED)
        if not isinstance(reload, list) or not reload or not all(isinstance(part, str) and part for part in reload):
            raise EngineError("host config proxy.reload must be a command list", EXIT_REFUSED)
        if not isinstance(tls, bool):
            raise EngineError("host config proxy.tls must be true or false", EXIT_REFUSED)
        return cls(Path(sites), reload, tls)


DELIVERY_REGISTRY = "registry"
DELIVERY_DIRECT = "direct"


@dataclass
class HostConfig:
    project: str
    environment: str
    base: Path
    pointer: str | None
    public_key: Path | None
    registry_credentials: Path | None = None
    lock_wait_seconds: int = 0
    notify: Path | None = None
    proxy: ProxyConfig | None = None
    delivery: str = DELIVERY_REGISTRY

    @classmethod
    def load(cls, path: Path) -> HostConfig:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        allowed = {
            "project", "environment", "base", "pointer", "public_key", "registry_credentials", "lock_wait_seconds",
            "notify", "proxy", "delivery",
        }
        unknown = set(data) - allowed
        if unknown:
            raise EngineError(f"host config has unknown keys: {', '.join(sorted(unknown))}", EXIT_REFUSED)
        delivery = data.get("delivery", DELIVERY_REGISTRY)
        if delivery == DELIVERY_REGISTRY:
            if "pointer" not in data or "public_key" not in data:
                raise EngineError("a registry host config needs pointer and public_key", EXIT_REFUSED)
        elif delivery == DELIVERY_DIRECT:
            if {"pointer", "public_key", "registry_credentials"} & set(data):
                raise EngineError("a direct host config takes no pointer, public_key, or registry_credentials", EXIT_REFUSED)
        else:
            raise EngineError("host config delivery must be registry or direct", EXIT_REFUSED)
        try:
            config = cls(
                project=data["project"],
                environment=data["environment"],
                base=Path(data["base"]),
                pointer=data.get("pointer"),
                public_key=Path(data["public_key"]) if "public_key" in data else None,
                registry_credentials=Path(data["registry_credentials"]) if data.get("registry_credentials") else None,
                lock_wait_seconds=int(data.get("lock_wait_seconds", 0)),
                notify=Path(data["notify"]) if data.get("notify") else None,
                proxy=ProxyConfig.parse(data["proxy"]) if "proxy" in data else None,
                delivery=delivery,
            )
        except (KeyError, TypeError, ValueError):
            raise EngineError("host config needs project, environment, and base", EXIT_REFUSED) from None
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
Query = Callable[[list[str], dict[str, str]], tuple[int, str]]
HealthProbe = Callable[[str], dict | None]


def run_command(arguments: list[str], env: dict[str, str], cwd: Path | None, timeout: int) -> int:
    """Run a fixed argument list; its output goes to the log, its environment is minimal."""
    try:
        return subprocess.run(arguments, env=env, cwd=cwd, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        log(f"{arguments[0]} {arguments[1] if len(arguments) > 1 else ''} timed out")
        return 124


def run_query(arguments: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run a read-only docker query and return its exit code and standard output."""
    try:
        result = subprocess.run(arguments, env=env, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return 124, ""
    return result.returncode, result.stdout


def probe_health(url: str) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=3) as response:
            body = json.loads(response.read(65536))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


class ReleaseSource:
    """Where the engine gets a release from: the registry (ADR 0009) or a direct delivery
    (ADR 0010, af_receive.py). Each method acts before anything running is changed."""

    def fetch(self) -> af_release.Release:
        """The verified release record to deploy."""
        raise NotImplementedError

    def unpack(self, record: dict, destination: Path) -> dict:
        """Write the release's deploy bundle into `destination`; return its release.toml."""
        raise NotImplementedError

    def same_files(self, running: dict, record: dict) -> bool:
        """Whether a new record for the running commit carries the same deploy files."""
        raise NotImplementedError

    def load_images(self, record: dict, env: dict[str, str], runner: Runner, query: Query) -> str | None:
        """Make the record's images available locally; the reason when one is not."""
        raise NotImplementedError

    def discard(self, record: dict, in_use: set[str], runner: Runner) -> None:
        """Free the images of a release retention removed (never one still in use)."""
        raise NotImplementedError


class ReceiverOnly(ReleaseSource):
    """A direct environment seen from the engine's own commands (status, monitor)."""

    def fetch(self) -> af_release.Release:
        raise EngineError("direct releases arrive through af_receive.py, not af_engine.py deploy", EXIT_REFUSED)


class RegistrySource(ReleaseSource):
    def __init__(self, host: HostConfig, registry: af_oci.Registry, verify: af_release.Verify) -> None:
        self.host = host
        self.registry = registry
        self.verify = verify

    def fetch(self) -> af_release.Release:
        return af_release.fetch_release(self.registry, self.host.pointer, self.host.project, self.verify)

    def unpack(self, record: dict, destination: Path) -> dict:
        return af_release.fetch_bundle(self.registry, record, destination)

    def same_files(self, running: dict, record: dict) -> bool:
        return running["bundle"] == record["bundle"]

    def load_images(self, record: dict, env: dict[str, str], runner: Runner, query: Query) -> str | None:
        for reference in record["images"].values():
            if runner(["docker", "pull", "--quiet", reference], env, None, COMMAND_TIMEOUT) != 0:
                return "image pull failed"
        return None

    def discard(self, record: dict, in_use: set[str], runner: Runner) -> None:
        env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST") if key in os.environ}
        for image in record["images"].values():
            if image not in in_use:
                runner(["docker", "image", "rm", image], env, None, COMMAND_TIMEOUT)


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
        query: Query = run_query,
        health: HealthProbe = probe_health,
        sleep: Callable[[float], None] = time.sleep,
        health_attempts: int = HEALTH_ATTEMPTS,
        trigger: str = "pull",
        actor: str = "agent",
        placement_values: dict[str, str] | None = None,
        notifier: af_notify.Notifier | None = None,
        source: ReleaseSource | None = None,
    ) -> None:
        self.host = host
        if source is None and host.delivery == DELIVERY_DIRECT:
            source = ReceiverOnly()
        if source is None:
            if registry is None:
                credentials = (
                    af_oci.credentials_file(host.registry_credentials) if host.registry_credentials else af_oci.no_credentials
                )
                registry = af_oci.Registry(credentials)
            source = RegistrySource(host, registry, verify or af_release.CosignVerifier(host.public_key))
        self.source = source
        self.runner = runner
        self.query = query
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
        # The route file's content before this deployment changed it; None while unchanged.
        self.routes_before: str | None = None

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
            AF_SECRETS_GID=str(os.getgid()),
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

    # -- containers and health ---------------------------------------------------------

    def _docker_env(self) -> dict[str, str]:
        return {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if key in os.environ}

    def containers(self, service: str) -> list[str]:
        """IDs of the running containers of one service in this environment's Compose project."""
        code, output = self.query(
            [
                "docker", "ps", "--quiet", "--no-trunc",
                "--filter", f"label=com.docker.compose.project={self.host.compose_project}",
                "--filter", f"label=com.docker.compose.service={service}",
                "--filter", "status=running",
            ],
            self._docker_env(),
        )
        return sorted(output.split()) if code == 0 else []

    def loopback_port(self, container: str, port: int) -> int | None:
        """The host port Docker published for a container port, only if bound to 127.0.0.1."""
        code, output = self.query(["docker", "port", container, f"{port}/tcp"], self._docker_env())
        bindings = output.split() if code == 0 else []
        if not bindings or any(not binding.startswith("127.0.0.1:") for binding in bindings):
            return None
        return int(bindings[0].rsplit(":", 1)[1])

    def healthy(self, release: Release, containers: list[str] | None = None) -> bool:
        """Every running container of the health service (or the given ones) must report the
        release's commit on its own loopback port."""
        health = release.config["health"]
        for attempt in range(self.health_attempts):
            targets = containers if containers is not None else self.containers(health["service"])
            if targets and all(self._reports(release, target) for target in targets):
                return True
            if attempt + 1 < self.health_attempts:
                self.sleep(HEALTH_INTERVAL)
        return False

    def _reports(self, release: Release, container: str) -> bool:
        health = release.config["health"]
        port = self.loopback_port(container, health["port"])
        if port is None:
            return False
        body = self.health(f"http://127.0.0.1:{port}{health['path']}")
        return body is not None and body.get(health["revision_key"]) == release.record["commit"]

    # -- proxy routes ------------------------------------------------------------------

    @property
    def route_file(self) -> Path | None:
        proxy = self.host.proxy
        return proxy.sites / f"{self.host.compose_project}.caddy" if proxy else None

    def route_domains(self, release: Release) -> list[str] | None:
        """Each route's domain, checked before anything stops; None when this host or project
        has no routes. A placement value can never inject proxy configuration."""
        routes = release.config.get("routes") or []
        if self.host.proxy is None or not routes:
            return None
        if not self.host.proxy.sites.is_dir():
            raise EngineError("proxy.sites is not a directory", EXIT_REFUSED)
        values = read_runtime(release.runtime)
        domains = []
        for route in routes:
            domain = values.get(route["host_setting"], "")
            if not DOMAIN.fullmatch(domain):
                raise EngineError(f"route {route['host_setting']} must hold a lowercase domain name", EXIT_REFUSED)
            domains.append(domain)
        return domains

    def route_text(self, release: Release, domains: list[str] | None, chosen: dict[str, list[str]]) -> str | None:
        """The proxy site file: each domain to the loopback ports of its service's containers
        (`chosen` names the containers per service; others are looked up)."""
        if domains is None:
            return None
        blocks = []
        for domain, route in zip(domains, release.config["routes"], strict=True):
            targets = chosen.get(route["service"]) or self.containers(route["service"])
            ports = [self.loopback_port(target, route["port"]) for target in targets]
            if not ports or None in ports:
                raise EngineError(f"service {route['service']} publishes no loopback port {route['port']}", EXIT_FAILED)
            address = domain if self.host.proxy.tls else f"http://{domain}"
            upstreams = " ".join(f"127.0.0.1:{port}" for port in ports)
            blocks.append(f"{address} {{\n\treverse_proxy {upstreams}\n}}\n")
        header = (
            f"# Managed by af_engine for {self.host.project} {self.host.environment}; "
            "changes here are overwritten.\n"
        )
        return header + "\n".join(blocks)

    def write_routes(self, text: str | None) -> bool:
        """Install the release's routes and reload the proxy; on a failed reload the old
        file is put back. True when the routes are in place (or nothing changed)."""
        path = self.route_file
        if text is None or path is None:
            return True
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old == text:
            return True
        log("updating proxy routes")
        write_text(path, text, 0o644)
        if self.reload_proxy():
            self.routes_before = old
            return True
        self._put_routes_back(old)
        return False

    def restore_routes(self) -> None:
        """Undo this deployment's route change (used when the old release is restored)."""
        if self.routes_before is not None:
            self._put_routes_back(self.routes_before)
            self.routes_before = None

    def reload_proxy(self) -> bool:
        env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        return self.runner(list(self.host.proxy.reload), env, None, RELOAD_TIMEOUT) == 0

    def _put_routes_back(self, text: str) -> None:
        path = self.route_file
        if text:
            write_text(path, text, 0o644)
        else:
            path.unlink(missing_ok=True)
        if not self.reload_proxy():
            log("proxy reload failed while putting the previous routes back. Check the proxy")

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
            if not self.source.same_files(current.record, release.record):
                raise EngineError("the running commit was republished with a different bundle", EXIT_REFUSED)
            write_json(directory / "record.json", release.record)
            return Release(directory, release.record, current.config)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(mode=0o700, parents=True)
        config = self.source.unpack(release.record, directory / "bundle")
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
        for directory in directories[keep:]:
            if directory.name in protect:
                continue
            record_path = directory / "record.json"
            if record_path.exists():
                self.source.discard(json.loads(record_path.read_text(encoding="utf-8")), in_use, self.runner)
            shutil.rmtree(directory)

    # -- the sequence ------------------------------------------------------------------

    def deploy(self, lock_held: bool = False) -> dict:
        """Deploy under the environment's lock; a caller that took it already (the direct
        receiver holds it while it builds) passes lock_held."""
        check_private_directory(self.base, "base")
        self.releases.mkdir(mode=0o700, exist_ok=True)
        if not self.secrets.exists():
            self.secrets.mkdir(mode=0o700)
        started = utc_now()
        if lock_held:
            return self._deploy(started)
        lock = acquire_lock(self.base, self.host.lock_wait_seconds, self.sleep)
        try:
            return self._deploy(started)
        finally:
            os.close(lock)

    def _deploy(self, started: str) -> dict:
        previous = self.current()
        try:
            fetched = self.source.fetch()
            order = af_release.check_order(previous.record if previous else None, fetched.record)
        except af_release.ReleaseError as error:
            raise EngineError(str(error), EXIT_REFUSED) from None
        if order == "unchanged":
            log("this release already runs. Nothing to do")
            return self._record("unchanged", EXIT_OK, fetched, previous, started, backup=None)
        log(f"deploying {fetched.record['commit'][:12]} ({fetched.record['version'] or 'unversioned'})")
        try:
            candidate = self.prepare(fetched, previous)
            if candidate.config.get("switch", "stop-start") == "blue-green" and self.host.proxy is None:
                raise EngineError("blue-green switching needs a [proxy] on this host", EXIT_REFUSED)
            settings = af_settings.build_runtime(
                candidate.bundle,
                self.host.environment,
                self.secrets,
                candidate.runtime,
                placement_file=None if self.placement_values is not None else self.base / "placement.env",
                placement_values=self.placement_values,
            )
            domains = self.route_domains(candidate)
        except (af_release.ReleaseError, af_settings.SettingsError) as error:
            raise EngineError(str(error), EXIT_REFUSED) from None
        if settings.generated:
            log(f"generated secrets: {', '.join(settings.generated)}")

        # Nothing has changed yet: failures here leave the running release untouched.
        missing = self.source.load_images(candidate.record, self._env(candidate), self.runner, self.query)
        if missing is not None:
            return self._fail_early(missing, fetched, previous, started)
        if self.compose(candidate, "config", "--quiet") != 0:
            return self._fail_early("compose configuration is invalid", fetched, previous, started)
        preflight = candidate.config.get("preflight") or []
        if preflight:
            service = candidate.config.get("preflight_service", candidate.config["health"]["service"])
            if self.compose(candidate, "run", "--rm", "--no-deps", service, *preflight) != 0:
                return self._fail_early("preflight refused the configuration", fetched, previous, started)
        if not self.hook(candidate, "pre_migrate"):
            return self._fail_early("pre_migrate hook failed", fetched, previous, started)

        # stop-start: from here the old release is stopped; failures restore it or stop for the
        # owner. blue-green: the old release keeps serving until the routes have moved.
        blue_green = candidate.config.get("switch", "stop-start") == "blue-green" and previous is not None
        drain = int(candidate.config.get("drain_seconds", 60))
        if previous is not None and not blue_green:
            log("stopping the previous release")
            self.compose(previous, "stop", "--timeout", str(drain))

        def fail(reason: str, backup: str | None) -> dict:
            if blue_green:
                return self._fail_beside(reason, [], fetched, previous, started, backup)
            return self._fail_late(reason, candidate, fetched, previous, started, backup)

        backup = None
        migrations = bool(candidate.config.get("migrate"))
        environment = candidate.config["environments"].get(self.host.environment, {})
        backup_required = migrations and environment.get("backup", "optional") == "required"
        hook_present = (candidate.bundle / "deploy/hooks/backup").exists()
        if backup_required and not hook_present:
            return fail("backup is required but the bundle has no backup hook", None)
        if hook_present and previous is not None:
            backup_dir = self.base / "backups" / started.replace(":", "").replace("-", "")
            backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.hook(candidate, "backup", {"AF_BACKUP_DIR": str(backup_dir)}) or not any(backup_dir.iterdir()):
                return fail("backup hook failed or wrote nothing", None)
            backup = str(backup_dir)
        if migrations:
            service = candidate.config.get("migrate_service", candidate.config["health"]["service"])
            # With its Compose dependencies: a first deployment has no database running yet.
            if self.compose(candidate, "run", "--rm", service, *candidate.config["migrate"]) != 0:
                return fail("migration failed", backup)
        if blue_green:
            failure = self._switch_beside(candidate, domains, drain, fetched, previous, started, backup)
            if failure is not None:
                return failure
        if self.compose(candidate, "up", "--detach", "--no-build", "--remove-orphans") != 0:
            return self._fail_late("start failed", candidate, fetched, previous, started, backup)
        if not self.healthy(candidate):
            return self._fail_late("health check did not report this release", candidate, fetched, previous, started, backup)
        try:
            routes = self.route_text(candidate, domains, {})
        except EngineError as error:
            return self._fail_late(str(error), candidate, fetched, previous, started, backup)
        if not self.write_routes(routes):
            return self._fail_late("the proxy refused the new routes", candidate, fetched, previous, started, backup)
        if not self.hook(candidate, "post_switch") or not self.hook(candidate, "smoke"):
            return self._fail_late("post-switch or smoke hook failed", candidate, fetched, previous, started, backup)

        self.promote(candidate)
        keep = int(environment.get("keep", 3))
        protect = {candidate.record["commit"]} | ({previous.record["commit"]} if previous else set())
        self.retain(keep, protect)
        log("deployed")
        return self._record("deployed", EXIT_OK, fetched, previous, started, backup)

    def _switch_beside(
        self,
        candidate: Release,
        domains: list[str] | None,
        drain: int,
        fetched: af_release.Release,
        previous: Release | None,
        started: str,
        backup: str | None,
    ) -> dict | None:
        """Blue-green: start each routed service beside its running containers, check the new
        ones, move the routes to them, then stop the old ones. A failure before the routes
        move removes only the new containers. Returns a failure record, or None."""
        services = sorted({route["service"] for route in candidate.config["routes"]})
        old = {service: self.containers(service) for service in services}
        log(f"starting {', '.join(services)} beside the running release")
        for service in services:
            count = len(old[service]) + max(len(old[service]), 1)
            arguments = ("up", "--detach", "--no-build", "--no-deps", "--no-recreate", "--scale", f"{service}={count}", service)
            if self.compose(candidate, *arguments) != 0:
                return self._fail_beside("start beside the running release failed", self._new(services, old),
                                         fetched, previous, started, backup)
        new = self._new(services, old)
        started_containers = [container for containers in new.values() for container in containers]
        if not self.healthy(candidate, new[candidate.config["health"]["service"]]):
            return self._fail_beside("health check did not report this release", started_containers,
                                     fetched, previous, started, backup)
        try:
            text = self.route_text(candidate, domains, new)
        except EngineError as error:
            return self._fail_beside(str(error), started_containers, fetched, previous, started, backup)
        if not self.write_routes(text):
            return self._fail_beside("the proxy refused the new routes", started_containers,
                                     fetched, previous, started, backup)
        stale = [container for service in services for container in old[service]]
        if stale:
            log("routes moved. Stopping the previous containers")
            env = self._docker_env()
            self.runner(["docker", "stop", "--time", str(drain), *stale], env, None, COMMAND_TIMEOUT)
            self.runner(["docker", "rm", *stale], env, None, COMMAND_TIMEOUT)
        return None

    def _new(self, services: list[str], old: dict[str, list[str]]) -> dict[str, list[str]]:
        return {service: [c for c in self.containers(service) if c not in old[service]] for service in services}

    def _fail_beside(
        self,
        reason: str,
        started_containers: list[str],
        fetched: af_release.Release,
        previous: Release | None,
        started: str,
        backup: str | None,
    ) -> dict:
        log(f"{reason}; the running release keeps serving")
        if started_containers:
            self.runner(["docker", "rm", "--force", *started_containers], self._docker_env(), None, COMMAND_TIMEOUT)
        return self._record("failed", EXIT_FAILED, fetched, previous, started, backup, reason)

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
            log("first deployment failed. Services stay stopped")
            return self._record("failed", EXIT_FAILED, fetched, previous, started, backup, reason)
        if candidate.record["schema_version"] != previous.record["schema_version"]:
            log("the schema changed. Services stay stopped for the owner (restore from the backup)")
            return self._record("stopped", EXIT_STOPPED, fetched, previous, started, backup, reason)
        log("restoring the previous release")
        self.restore_routes()
        if self.compose(previous, "up", "--detach", "--no-build", "--remove-orphans") == 0 and self.healthy(previous):
            log("previous release restored")
            return self._record("restored", EXIT_RESTORED, fetched, previous, started, backup, reason)
        log("restore failed. Act manually")
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


def acquire_lock(base: Path, wait_seconds: int, sleep: Callable[[float], None] = time.sleep) -> int:
    """The environment's deployment lock, as an open descriptor; busy after wait_seconds."""
    descriptor = os.open(base / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise EngineError("another deployment holds the lock", EXIT_BUSY) from None
            sleep(1.0)


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


def write_text(path: Path, text: str, mode: int) -> None:
    """Replace a file atomically with the given mode (no symlink is followed)."""
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(temporary, flags, mode), "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


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
