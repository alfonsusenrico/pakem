#!/usr/bin/env python3
"""Direct delivery (ADR 0010): a device's signed release, built and deployed on this host.

    af_receive.py serve --device NAME --project P [--project ...]
        The forced command of a device key in ~af/.ssh/authorized_keys:

            restrict,command="/usr/bin/python3 /opt/af/af_receive.py serve --device laptop --project example" ssh-ed25519 AAAA... pakem-device laptop

        SSH_ORIGINAL_COMMAND may only be `release`, `history <project>`, or
        `status <project>`. `release` reads one line of request JSON, one line with its
        signature (`ssh-keygen -Y sign -n pakem-release`, base64), and then, when the
        request names one, the `git archive` tar of its commit. The request must be fresh,
        come from this device, and be signed by this device's key for the project
        (/etc/af/allowed_signers, principals = projects). A new commit is built here once,
        for this host, with CPU and memory limits and one build at a time; promotion and
        rollback reuse the images a kept release already has. Then the engine deploys it.

    af_receive.py authorize --device NAME --key "ssh-ed25519 AAAA..." --project P
                            --environment E [...] --deploy-root D [--proxy]
                            [--switch-from-registry] [--placement NAME ...] [--secret NAME ...]
    af_receive.py revoke --device NAME [--project P]
        Administration, as root, from `pakem host allow-device` and `revoke-device`
        (the owner's admin login). `authorize` writes the project's direct host configs and
        deploy bases, the device's authorized_keys line, and its allowed_signers line, and
        names the placement and secret settings still missing (names only).

Output names steps, projects, and settings, never values. Exit codes are the engine's.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import io
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import af_engine
import af_publish
import af_release
import contracts

NAMESPACE = "pakem-release"
DEPLOY_USER = "af"
CONFIG_DIR = Path("/etc/af/hosts")
ALLOWED_SIGNERS = Path("/etc/af/allowed_signers")
NOTIFY_FILE = Path("/etc/af/notify.toml")
CADDY_SITES = "/etc/caddy/sites"
CADDY_RELOAD = ["caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--address", "unix//run/caddy/admin.sock"]
RECEIVER = "/opt/af/af_receive.py"
KEY_COMMENT = "pakem-device"
# A signed request is accepted for this long after it was made (and as far ahead, for clocks).
FRESH_SECONDS = 600
MAX_LINE = 64 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_EXPANDED = 4 * 1024 * 1024 * 1024
MAX_SOURCE_ENTRIES = 200_000
BUILD_TIMEOUT = 3600
# Builds run in their own BuildKit container so they cannot take the host from production.
BUILDER = "pakem-build"
BUILDER_LIMITS = ("memory=4g", "cpu-period=100000", "cpu-quota=200000")
BUILD_CACHE_FILTER = "until=168h"
PUBLIC_KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}")
ACTOR_PREFIX = "device:"


class ReceiveError(Exception):
    def __init__(self, message: str, exit_code: int = af_engine.EXIT_REFUSED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def log(message: str) -> None:
    print(f"af-receive: {message}", flush=True)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def seconds_from_now(stamp: str) -> float:
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return moment.timestamp() - time.time()


def docker_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if key in os.environ}


def image_tag(project: str, name: str, commit: str) -> str:
    """The local tag that keeps a built image while a release of it is kept."""
    return f"pakem/{project}-{name}:{commit}"


# -- the project's environments on this host ---------------------------------------------


def direct_hosts(config_dir: Path, project: str) -> dict[str, af_engine.HostConfig]:
    """The project's environments on this host that take direct releases."""
    hosts = {}
    for path in sorted(config_dir.glob(f"{project}-*.toml")):
        host = af_engine.HostConfig.load(path)
        if host.project == project and host.delivery == af_engine.DELIVERY_DIRECT:
            hosts[host.environment] = host
    return hosts


def deployments(host: af_engine.HostConfig) -> list[dict]:
    path = host.base / "deployments.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def kept_record(host: af_engine.HostConfig, commit: str) -> dict | None:
    """The record of a release this environment still keeps, or None."""
    directory = host.base / "releases" / commit
    path = directory / "record.json"
    if not path.is_file() or not (directory / "bundle" / "deploy" / "release.toml").is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if record.get("schema") == contracts.DIRECT_SCHEMA else None


def running_record(host: af_engine.HostConfig) -> dict | None:
    """The direct record of the release `current` points at, or None."""
    link = host.base / "current"
    if not link.is_symlink():
        return None
    return kept_record(host, Path(os.readlink(link)).name)


def current_release(host: af_engine.HostConfig) -> dict | None:
    """The record of the release `current` points at, however it was delivered (a release
    from the registry, before this environment moved to direct delivery, counts too)."""
    link = host.base / "current"
    if not link.is_symlink():
        return None
    path = host.base / os.readlink(link) / "record.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def kept_commits(host: af_engine.HostConfig) -> list[str]:
    releases = host.base / "releases"
    if not releases.is_dir():
        return []
    return sorted(path.name for path in releases.iterdir() if kept_record(host, path.name) is not None)


# -- the release source the engine deploys from ------------------------------------------


@dataclass
class Prepared:
    """A release's files and images, ready for the engine."""

    record: dict
    bundle: Path


class DirectSource(af_engine.ReleaseSource):
    def __init__(self, prepared: Prepared, environment: str, siblings: dict[str, af_engine.HostConfig]) -> None:
        self.prepared = prepared
        self.environment = environment
        self.siblings = siblings

    def fetch(self) -> af_release.Release:
        record = self.prepared.record
        running = running_record(self.siblings[self.environment])
        if (
            running is not None
            and not record["rollback"]
            and (running["commit"], running["version"]) == (record["commit"], record["version"])
        ):
            # Asking again for the release that runs changes nothing.
            return af_release.Release(running["request"], running)
        return af_release.Release(record["request"], record)

    def unpack(self, record: dict, destination: Path) -> dict:
        shutil.copytree(self.prepared.bundle, destination, symlinks=False)
        destination.chmod(0o700)
        return tomllib.loads((destination / "deploy/release.toml").read_text(encoding="utf-8"))

    def same_files(self, running: dict, record: dict) -> bool:
        return running.get("archive") == record["archive"]

    def load_images(self, record: dict, env: dict[str, str], runner: af_engine.Runner, query: af_engine.Query) -> str | None:
        for name, image in record["images"].items():
            code, output = query(["docker", "image", "inspect", "--format", "{{.Id}}", image], env)
            if code != 0 or output.strip() != image:
                return f"image {name} of this release is no longer on this host"
        return None

    def discard(self, record: dict, in_use: set[str], runner: af_engine.Runner) -> None:
        """Untag the release's images unless another environment keeps the same commit; an
        image disappears with its last tag, and Docker keeps any a container still uses."""
        for environment, host in self.siblings.items():
            if environment != self.environment and kept_record(host, record["commit"]) is not None:
                return
        for name in record["images"]:
            runner(["docker", "image", "rm", image_tag(record["project"], name, record["commit"])], docker_env(), None, 600)


# -- receiving a signed request ----------------------------------------------------------


def read_line(stream: io.BufferedReader, label: str) -> bytes:
    line = stream.readline(MAX_LINE + 1)
    if len(line) > MAX_LINE or not line.endswith(b"\n"):
        raise ReceiveError(f"the {label} line is missing or too long")
    return line[:-1]


def allowed_signers_for(signers: Path, device: str) -> str:
    """This device's lines of the allowed signers file: only its own key may sign here."""
    lines = []
    for line in signers.read_text(encoding="utf-8").splitlines():
        if line.strip() and line.split()[-2:] == [KEY_COMMENT, device]:
            lines.append(line)
    if not lines:
        raise ReceiveError(f"device {device} may not sign releases on this host")
    return "\n".join(lines) + "\n"


def verify_signature(signers: Path, device: str, project: str, request: bytes, signature: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="af-receive-") as work:
        allowed = Path(work, "allowed_signers")
        allowed.write_text(allowed_signers_for(signers, device), encoding="utf-8")
        signature_path = Path(work, "request.sig")
        signature_path.write_bytes(signature)
        completed = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", project, "-n", NAMESPACE, "-s", str(signature_path)],
            input=request,
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        raise ReceiveError(f"the request is not signed by device {device}'s key for {project}")


def check_request(request: dict, device: str, projects: list[str]) -> None:
    problems = contracts.validate_release_request(request)
    if problems:
        raise ReceiveError("; ".join(problems))
    if request["device"] != device:
        raise ReceiveError("the request names another device")
    if request["project"] not in projects:
        raise ReceiveError(f"device {device} may not release {request['project']} here")
    if abs(seconds_from_now(request["created"])) > FRESH_SECONDS:
        raise ReceiveError("the request is not fresh; check the device clock and send it again")


def receive_archive(stream: io.BufferedReader, destination: Path) -> str:
    """Copy the archive from standard input, within its size limit; returns its digest."""
    digest = hashlib.sha256()
    size = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(destination, flags, 0o600), "wb") as handle:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise ReceiveError("the archive is larger than the receiver accepts")
            digest.update(chunk)
            handle.write(chunk)
    if size == 0:
        raise ReceiveError("the request names an archive but none was sent")
    return f"sha256:{digest.hexdigest()}"


def _stays_inside(path: PurePosixPath) -> bool:
    """Whether a relative path, with its `..` parts applied, stays below its root."""
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if not parts:
                return False
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return True


def extract_source(archive: Path, destination: Path) -> None:
    """Unpack a `git archive` tar: files, directories, and symlinks that stay inside.

    The tree keeps git's modes (0644, 0755): Docker copies them into the images, whose
    users are often not root. It lives inside the private build directory, so nobody else
    on the host can read it."""
    destination.mkdir(mode=0o755)
    total = 0
    try:
        source = tarfile.open(archive, mode="r:")
    except tarfile.TarError:
        raise ReceiveError("the archive is not an uncompressed tar") from None
    with source:
        members = source.getmembers()
        if len(members) > MAX_SOURCE_ENTRIES:
            raise ReceiveError("the archive has too many entries")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in member.name:
                raise ReceiveError("an archive entry escapes the source tree")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
            elif member.isreg():
                total += member.size
                if total > MAX_SOURCE_EXPANDED:
                    raise ReceiveError("the archive expands beyond its size limit")
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                handle = source.extractfile(member)
                if handle is None:
                    raise ReceiveError("an archive entry cannot be read")
                mode = 0o755 if member.mode & 0o100 else 0o644
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                with handle, os.fdopen(os.open(target, flags, mode), "wb") as output:
                    shutil.copyfileobj(handle, output, 1024 * 1024)
            elif member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or not _stays_inside(path.parent / link):
                    raise ReceiveError("an archive symlink points outside the source tree")
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                os.symlink(member.linkname, target)
            else:
                raise ReceiveError("the archive may hold only files, directories, and symlinks")


def load_project(source: Path, project: str, environment: str) -> dict:
    try:
        config = af_publish.load_config(source)
    except (af_publish.PublishError, OSError, ValueError) as error:
        raise ReceiveError(f"deploy files of the commit: {error}") from None
    if config["project"] != project:
        raise ReceiveError("deploy/release.toml names another project")
    if environment not in config["environments"]:
        raise ReceiveError(f"deploy/release.toml declares no [environments.{environment}]")
    return config


class Builder:
    """Builds a commit's images natively, one build at a time on this host."""

    def __init__(self, lock_path: Path, run=subprocess.run) -> None:
        self.lock_path = lock_path
        self.run = run

    def _docker(self, *arguments: str, timeout: int = BUILD_TIMEOUT) -> subprocess.CompletedProcess:
        return self.run(
            ["docker", *arguments], env=docker_env(), capture_output=True, text=True, timeout=timeout, check=False
        )

    def _ensure_builder(self) -> None:
        if self._docker("buildx", "inspect", BUILDER, timeout=60).returncode == 0:
            return
        options = [part for limit in BUILDER_LIMITS for part in ("--driver-opt", limit)]
        created = self._docker("buildx", "create", "--name", BUILDER, "--driver", "docker-container", *options, timeout=300)
        if created.returncode != 0:
            raise ReceiveError("could not create the build container", af_engine.EXIT_FAILED)

    def build(self, source: Path, config: dict, project: str, commit: str) -> dict[str, str]:
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            log("waiting for this host's build slot")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._ensure_builder()
            images = {}
            for image in config["images"]:
                images[image["name"]] = self._build_one(source, image, project, commit)
            self._docker("buildx", "prune", "--builder", BUILDER, "--force", "--filter", BUILD_CACHE_FILTER, timeout=600)
            return images
        finally:
            os.close(descriptor)

    def _build_one(self, source: Path, image: dict, project: str, commit: str) -> str:
        name = image["name"]
        tag = image_tag(project, name, commit)
        log(f"building {name}")
        arguments = [
            "buildx", "build", "--builder", BUILDER, "--load", "--progress", "plain",
            "--build-arg", f"AF_REVISION={commit}", "--label", f"org.opencontainers.image.revision={commit}",
            "--file", str(source / image.get("dockerfile", "Dockerfile")), "--tag", tag,
        ]
        if image.get("target"):
            arguments += ["--target", image["target"]]
        built = self._docker(*arguments, str(source / image.get("context", ".")))
        if built.returncode != 0:
            for line in (built.stdout + built.stderr).strip().splitlines()[-20:]:
                print(f"  {line}", flush=True)
            raise ReceiveError(f"building {name} failed", af_engine.EXIT_FAILED)
        inspected = self._docker("image", "inspect", "--format", "{{.Id}}", tag, timeout=60)
        identifier = inspected.stdout.strip()
        if inspected.returncode != 0 or not contracts.DIGEST.fullmatch(identifier):
            raise ReceiveError(f"docker reported no image ID for {name}", af_engine.EXIT_FAILED)
        return identifier


def copy_bundle(source: Path, config: dict, destination: Path) -> None:
    """The deploy bundle (contracts §2) of the commit: its Compose files and deploy/."""
    try:
        names = af_publish.bundle_files(source, config)
    except af_publish.PublishError as error:
        raise ReceiveError(str(error)) from None
    destination.mkdir(mode=0o700)
    for name in names:
        target = destination / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = 0o700 if os.access(source / name, os.X_OK) else 0o600
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with open(source / name, "rb") as handle, os.fdopen(os.open(target, flags, mode), "wb") as output:
            shutil.copyfileobj(handle, output)


class Receiver:
    def __init__(
        self,
        device: str,
        projects: list[str],
        *,
        config_dir: Path = CONFIG_DIR,
        signers: Path = ALLOWED_SIGNERS,
        builder: Builder | None = None,
        engine=af_engine.Engine,
    ) -> None:
        self.device = device
        self.projects = projects
        self.config_dir = config_dir
        self.signers = signers
        self.builder = builder or Builder(Path.home() / "build.lock")
        self.engine = engine

    def serve(self, command: str | None, stream: io.BufferedReader) -> int:
        words = (command or "").split()
        try:
            if words == ["release"]:
                return self.release(stream)
            if len(words) == 2 and words[0] in ("history", "status"):
                return self.report(words[0], words[1])
            raise ReceiveError("expected `release`, `history <project>`, or `status <project>`")
        except (ReceiveError, af_engine.EngineError) as error:
            log(f"refused: {error}" if error.exit_code == af_engine.EXIT_REFUSED else str(error))
            return error.exit_code

    def _hosts(self, project: str) -> dict[str, af_engine.HostConfig]:
        if project not in self.projects:
            raise ReceiveError(f"device {self.device} may not release {project} here")
        hosts = direct_hosts(self.config_dir, project)
        if not hosts:
            raise ReceiveError(f"{project} takes no direct releases on this host; run `pakem host allow-device`")
        return hosts

    def report(self, kind: str, project: str) -> int:
        hosts = self._hosts(project)
        result: dict = {"project": project, "environments": {}}
        for environment, host in hosts.items():
            running = current_release(host)
            entry = {
                "current": af_engine.read_json(host.base / "deployment.json") or None,
                "running": running["commit"] if running else None,
            }
            if kind == "history":
                entry["deployments"] = deployments(host)
                entry["kept"] = kept_commits(host)
            result["environments"][environment] = entry
        print(json.dumps(result, indent=2, sort_keys=True))
        return af_engine.EXIT_OK

    def release(self, stream: io.BufferedReader) -> int:
        request_bytes = read_line(stream, "request")
        try:
            signature = base64.b64decode(read_line(stream, "signature"), validate=True)
            request = json.loads(request_bytes)
        except (binascii.Error, ValueError):
            raise ReceiveError("the request or its signature is malformed") from None
        check_request(request, self.device, self.projects)
        verify_signature(self.signers, self.device, request["project"], request_bytes, signature)
        hosts = self._hosts(request["project"])
        environment = request["environment"]
        if environment not in hosts:
            raise ReceiveError(f"{request['project']} has no direct {environment} environment on this host")
        host = hosts[environment]
        af_engine.check_private_directory(host.base, "base")
        digest = f"sha256:{hashlib.sha256(request_bytes).hexdigest()}"
        builds = host.base / "builds"
        builds.mkdir(mode=0o700, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"{request['commit'][:12]}-", dir=builds))
        try:
            prepared = self.prepare(request, digest, hosts, host, stream, work)
            source = DirectSource(prepared, environment, hosts)
            engine = self.engine(host, source=source, trigger="direct", actor=f"{ACTOR_PREFIX}{self.device}")
            record = engine.deploy()
        finally:
            shutil.rmtree(work, ignore_errors=True)
        log(f"result {record['result']} {record['commit'][:12]} {record['version'] or ''}".rstrip())
        return int(record["exit_code"])

    def prepare(
        self,
        request: dict,
        digest: str,
        hosts: dict[str, af_engine.HostConfig],
        host: af_engine.HostConfig,
        stream: io.BufferedReader,
        work: Path,
    ) -> Prepared:
        commit = request["commit"]
        archive = None
        if request["archive"] is not None:
            archive = work / "source.tar"
            if receive_archive(stream, archive) != request["archive"]:
                raise ReceiveError("the archive does not match the signed request")
        kept = self._kept(hosts, commit)
        action = request["action"]
        history = deployments(host)
        if action == "rollback":
            self._check_rollback(request, host, history, kept)
        if action == "promote":
            self._check_promotion(request, hosts, host, history)
        if kept is not None:
            kept_host, kept_entry = kept
            if archive is not None and kept_entry["archive"] != request["archive"]:
                raise ReceiveError("this commit was built here from a different archive")
            log(f"reusing the images built for {kept_host.environment}")
            # A copy: the engine replaces this environment's release directory before it unpacks.
            bundle = work / "bundle"
            shutil.copytree(kept_host.base / "releases" / commit / "bundle", bundle, symlinks=False)
            load_project(bundle, request["project"], host.environment)
            images, schema_version, archive_digest = kept_entry["images"], kept_entry["schema_version"], kept_entry["archive"]
        elif archive is None:
            raise ReceiveError(f"this host keeps no release of {commit[:12]}; send its archive")
        else:
            source = work / "source"
            extract_source(archive, source)
            archive.unlink()
            config = load_project(source, request["project"], host.environment)
            try:
                schema_version = af_publish.schema_version(source, config)
            except af_publish.PublishError as error:
                raise ReceiveError(str(error)) from None
            images = self.builder.build(source, config, request["project"], commit)
            bundle = work / "bundle"
            copy_bundle(source, config, bundle)
            archive_digest = request["archive"]
        version = request["version"]
        if action == "rollback":
            version = next((entry["version"] for entry in reversed(history)
                            if entry["commit"] == commit and entry["result"] == "deployed"), None)
        record = {
            "schema": contracts.DIRECT_SCHEMA,
            "project": request["project"],
            "commit": commit,
            "version": version,
            "created": utc_now(),
            "source": request["source"],
            "device": request["device"],
            "archive": archive_digest,
            "images": images,
            "schema_version": schema_version,
            "rollback": action == "rollback",
            "request": digest,
        }
        problems = contracts.validate_direct_record(record)
        if problems:
            raise ReceiveError("; ".join(problems), af_engine.EXIT_FAILED)
        return Prepared(record, bundle)

    @staticmethod
    def _kept(hosts: dict[str, af_engine.HostConfig], commit: str) -> tuple[af_engine.HostConfig, dict] | None:
        for host in hosts.values():
            record = kept_record(host, commit)
            if record is not None:
                return host, record
        return None

    @staticmethod
    def _check_rollback(request: dict, host: af_engine.HostConfig, history: list[dict], kept) -> None:
        commit = request["commit"]
        if not any(entry["commit"] == commit and entry["result"] == "deployed" for entry in history):
            raise ReceiveError(f"{commit[:12]} was never deployed to {host.environment} here")
        current = current_release(host) or {}
        if current.get("commit") == commit:
            raise ReceiveError(f"{commit[:12]} already runs in {host.environment}")
        target_schema = next((entry["schema_version"] for entry in reversed(history) if entry["commit"] == commit), None)
        if target_schema != current.get("schema_version") and not request["allow_schema"]:
            raise ReceiveError(
                "the rollback crosses a schema change; restore the database from its backup first, then pass --allow-schema"
            )

    @staticmethod
    def _check_promotion(request: dict, hosts, host: af_engine.HostConfig, history: list[dict]) -> None:
        for entry in history:
            if entry["version"] == request["version"] and entry["commit"] != request["commit"]:
                raise ReceiveError(f"{request['version']} already named another commit in {host.environment}")
        staging = hosts.get("staging")
        if host.environment != "staging" and staging is not None:
            if not any(entry["commit"] == request["commit"] and entry["result"] == "deployed"
                       for entry in deployments(staging)):
                raise ReceiveError(f"{request['commit'][:12]} never deployed to staging here; publish it first")


# -- administration (root) -----------------------------------------------------------------


def _chown(path: Path, user: str, group: str) -> None:
    if os.geteuid() == 0:
        shutil.chown(path, user, group)


def _deploy_user_home() -> Path:
    return Path(pwd.getpwnam(DEPLOY_USER).pw_dir)


def host_config_text(project: str, environment: str, base: Path, proxy: bool, notify: bool) -> str:
    lines = [
        "# Written by af_receive.py authorize (pakem host allow-device); direct delivery (ADR 0010).",
        f'project = "{project}"',
        f'environment = "{environment}"',
        f'base = "{base}"',
        'delivery = "direct"',
    ]
    if notify:
        lines.append(f'notify = "{NOTIFY_FILE}"')
    if proxy:
        reload = ", ".join(f'"{part}"' for part in CADDY_RELOAD)
        lines += ["", "[proxy]", f'sites = "{CADDY_SITES}"', f"reload = [{reload}]", "tls = true"]
    return "\n".join(lines) + "\n"


def _registry_to_direct(text: str) -> str:
    """Keep a registry config's own settings and switch it to direct delivery."""
    kept = [line for line in text.splitlines()
            if not re.match(r"\s*(pointer|public_key|registry_credentials|delivery)\s*=", line)]
    first_table = next((index for index, line in enumerate(kept) if line.lstrip().startswith("[")), len(kept))
    kept.insert(first_table, 'delivery = "direct"')
    return "\n".join(kept) + "\n"


def write_private(path: Path, text: str, mode: int, user: str, group: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(temporary, flags, mode), "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, mode)
    _chown(temporary, user, group)
    os.replace(temporary, path)


@dataclass
class DeviceLine:
    key: str
    projects: list[str]


def device_lines(text: str) -> dict[str, DeviceLine]:
    """Device name to its key and projects, from authorized_keys or allowed_signers."""
    devices = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) < 4 or words[-2] != KEY_COMMENT:
            continue
        match = re.search(r"(ssh-ed25519 [A-Za-z0-9+/]+={0,2})", line)
        if match is None:
            continue
        projects = re.findall(r"--project ([a-z][a-z0-9-]*)", line)
        if not projects:
            projects = words[0].split(",")
        devices[words[-1]] = DeviceLine(match[1], projects)
    return devices


def authorized_line(device: str, entry: DeviceLine) -> str:
    projects = " ".join(f"--project {project}" for project in entry.projects)
    command = f"/usr/bin/python3 {RECEIVER} serve --device {device} {projects}"
    return f'restrict,command="{command}" {entry.key} {KEY_COMMENT} {device}'


def signer_line(device: str, entry: DeviceLine) -> str:
    return f'{",".join(entry.projects)} namespaces="{NAMESPACE}" {entry.key} {KEY_COMMENT} {device}'


def rewrite_device_lines(path: Path, devices: dict[str, DeviceLine], render, mode: int, user: str, group: str) -> None:
    """Replace this tool's lines in a key file and keep every other line as it is."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    others = [line for line in text.splitlines() if not (len(line.split()) >= 4 and line.split()[-2] == KEY_COMMENT)]
    lines = others + [render(device, entry) for device, entry in sorted(devices.items()) if entry.projects]
    write_private(path, "\n".join(lines) + ("\n" if lines else ""), mode, user, group)


@dataclass
class KeyFiles:
    authorized_keys: Path
    signers: Path


def key_files() -> KeyFiles:
    return KeyFiles(_deploy_user_home() / ".ssh" / "authorized_keys", ALLOWED_SIGNERS)


def authorize(args, files: KeyFiles, config_dir: Path) -> int:
    if not PUBLIC_KEY.fullmatch(args.key):
        raise ReceiveError("--key must be one ssh-ed25519 public key")
    if not contracts.DEVICE.fullmatch(args.device) or not contracts.PROJECT.fullmatch(args.project):
        raise ReceiveError("invalid device or project name")
    if not args.environment or not all(contracts.ENVIRONMENT.fullmatch(name) for name in args.environment):
        raise ReceiveError("name at least one valid --environment")
    deploy_root = Path(args.deploy_root)
    if not deploy_root.is_absolute() or deploy_root == Path("/"):
        raise ReceiveError("--deploy-root must be an absolute path")
    existing = device_lines(files.authorized_keys.read_text(encoding="utf-8")) if files.authorized_keys.exists() else {}
    for name, entry in existing.items():
        if name == args.device and entry.key != args.key:
            raise ReceiveError(f"device {args.device} has another key here; revoke it first")
        if name != args.device and entry.key == args.key:
            raise ReceiveError(f"this key is already authorized as device {name}")

    project_root = deploy_root / args.project
    if not project_root.exists():
        project_root.mkdir(mode=0o700)
        _chown(project_root, DEPLOY_USER, DEPLOY_USER)
    for environment in args.environment:
        path = config_dir / f"{args.project}-{environment}.toml"
        base = project_root / environment
        if path.exists():
            text = path.read_text(encoding="utf-8")
            host = af_engine.HostConfig.load(path)
            if host.delivery == af_engine.DELIVERY_REGISTRY:
                if not args.switch_from_registry:
                    raise ReceiveError(f"{path} deploys from a registry; pass --switch-from-registry to move it to direct")
                write_private(path, _registry_to_direct(text), 0o640, "root", DEPLOY_USER)
                log(f"{args.project} {environment}: switched to direct delivery")
            base = host.base
        else:
            text = host_config_text(args.project, environment, base, args.proxy, NOTIFY_FILE.exists())
            write_private(path, text, 0o640, "root", DEPLOY_USER)
            log(f"{args.project} {environment}: host config {path}")
        for directory in (base, base / "secrets"):
            if not directory.exists():
                directory.mkdir(mode=0o700)
                _chown(directory, DEPLOY_USER, DEPLOY_USER)
        missing = [name for name in args.placement if name not in placement_names(base / "placement.env")]
        missing_secrets = [name for name in args.secret if not (base / "secrets" / name).is_file()]
        for kind, names in (("placement", missing), ("secret", missing_secrets)):
            if names:
                log(f"{args.project} {environment}: missing {kind} settings: {', '.join(names)}")

    entry = existing.get(args.device, DeviceLine(args.key, []))
    if args.project not in entry.projects:
        entry.projects.append(args.project)
    existing[args.device] = entry
    files.authorized_keys.parent.mkdir(mode=0o700, exist_ok=True)
    _chown(files.authorized_keys.parent, DEPLOY_USER, DEPLOY_USER)
    rewrite_device_lines(files.authorized_keys, existing, authorized_line, 0o600, DEPLOY_USER, DEPLOY_USER)
    rewrite_device_lines(files.signers, existing, signer_line, 0o640, "root", DEPLOY_USER)
    log(f"device {args.device} may release: {', '.join(entry.projects)}")
    return af_engine.EXIT_OK


def placement_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, _ = line.strip().partition("=")
        if separator and contracts.SETTING.fullmatch(name):
            names.add(name)
    return names


def revoke(args, files: KeyFiles) -> int:
    existing = device_lines(files.authorized_keys.read_text(encoding="utf-8")) if files.authorized_keys.exists() else {}
    if args.device not in existing:
        log(f"device {args.device} has no key here; nothing to do")
        return af_engine.EXIT_OK
    entry = existing[args.device]
    if args.project is None:
        entry.projects = []
    elif args.project in entry.projects:
        entry.projects.remove(args.project)
    rewrite_device_lines(files.authorized_keys, existing, authorized_line, 0o600, DEPLOY_USER, DEPLOY_USER)
    rewrite_device_lines(files.signers, existing, signer_line, 0o640, "root", DEPLOY_USER)
    if entry.projects:
        log(f"device {args.device} may still release: {', '.join(entry.projects)}")
    else:
        log(f"device {args.device} removed")
    return af_engine.EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_receive.py", description="Direct delivery receiver (ADR 0010)")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="the forced command of a device key")
    serve.add_argument("--device", required=True)
    serve.add_argument("--project", action="append", required=True)
    allow = commands.add_parser("authorize", help="as root: let a device release a project here")
    allow.add_argument("--device", required=True)
    allow.add_argument("--key", required=True)
    allow.add_argument("--project", required=True)
    allow.add_argument("--environment", action="append", default=[])
    allow.add_argument("--deploy-root", required=True)
    allow.add_argument("--proxy", action="store_true")
    allow.add_argument("--switch-from-registry", action="store_true")
    allow.add_argument("--placement", action="append", default=[])
    allow.add_argument("--secret", action="append", default=[])
    remove = commands.add_parser("revoke", help="as root: remove a device, or one of its projects")
    remove.add_argument("--device", required=True)
    remove.add_argument("--project")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            if not contracts.DEVICE.fullmatch(args.device) or not all(contracts.PROJECT.fullmatch(p) for p in args.project):
                raise ReceiveError("invalid device or project name in the forced command")
            receiver = Receiver(args.device, args.project)
            return receiver.serve(os.environ.get("SSH_ORIGINAL_COMMAND"), sys.stdin.buffer)
        if os.geteuid() != 0:
            raise ReceiveError("run as root (sudo)")
        if args.command == "authorize":
            return authorize(args, key_files(), CONFIG_DIR)
        return revoke(args, key_files())
    except ReceiveError as error:
        log(f"refused: {error}" if error.exit_code == af_engine.EXIT_REFUSED else str(error))
        return error.exit_code
    except (af_engine.EngineError, OSError) as error:
        log(f"refused: {error}")
        return af_engine.EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
