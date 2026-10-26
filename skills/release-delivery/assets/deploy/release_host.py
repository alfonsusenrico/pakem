#!/usr/bin/env python3
"""Release host tool: immutable per-commit releases for one environment on one host.

Runs on the deployment host, normally driven over SSH by deploy_remote.sh. Every
command fails closed: an unexpected owner, permission, digest, label, or state stops
the command with a non-zero exit and changes nothing that is already serving.

Commands (all take --base DIR and --owner root|self):
  lock         hold the environment lock (--hold --token T) or run a command under it
  secure-file  take custody of an uploaded file (moved into <base>/incoming, 0600)
  prepare      extract a commit archive safely into releases/<sha>/source (immutable,
               verified by tree digest) and install the runtime file for that release
  activate     build or verify images, preflight, isolated candidate check, production
               backup, migrations, start, local revision check; restore the previous
               release automatically when starting or the check fails
  promote      point `current` at the release after checking the running revision
  rollback     activate and promote a retained release without backup or migrations
  cleanup      remove releases beyond retention and their images (never current or running)
  record       write deployment.json and append to deployments.jsonl
  status       print the current release, retained releases, lock, and last record

Owner modes:
  root  organization hosts; must run as root (through a sudoers rule naming an
        installed copy of this file); everything is owned by root:root.
  self  personal servers; must run as the non-root deploy user, who owns the tree.

Exit codes: 0 success, 1 failure (nothing switched or nothing to restore), 2 invalid
input or state, 3 failure after which the previous release was restored, 4 failure
and the restore failed too (manual action needed), 75 the lock is held elsewhere.

Project contract: deploy/release.toml in the commit (see release.example.toml) and an
optional deploy/hooks/{pre_build,backup,pre_migrate,post_promote} executable.
Requires Python 3.11 or later (tomllib) and Docker with the Compose plugin.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.stderr.write("release_host: ERROR: Python 3.11 or later is required\n")
    sys.exit(2)

import argparse
import errno
import fcntl
import hashlib
import http.client
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_RESTORED = 3
EXIT_RESTORE_FAILED = 4
EXIT_LOCK_BUSY = 75

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ENVIRONMENT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
APP_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SERVICE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
VARIABLE_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,100}$")
PIPELINE_URL_PATTERN = re.compile(r"^https?://[A-Za-z0-9._~:/-]{1,400}$")
LOCAL_URL_PATTERN = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\]):\d{1,5}$")
# Runtime files are written by materialize_runtime.py: NAME='value', one per line.
RUNTIME_LINE = re.compile(r"^([A-Z_][A-Z0-9_]*)='([^'\n\r\x00]*)'$")
HOST_GENERATED_HEADER = "# af:host-generated"

REVISION_LABEL = "org.opencontainers.image.revision"
APP_LABEL = "io.agent-framework.app"
HOOK_NAMES = ("pre_build", "backup", "pre_migrate", "post_promote")
RECORD_RESULTS = ("succeeded", "failed", "restored", "restore-failed")
RECORD_MODES = ("deploy", "rollback")
DEFAULT_KEEP = {"production": 6}
FALLBACK_KEEP = 3
HOOK_TIMEOUT_SECONDS = 1800
COMMAND_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
# Variables passed from the host session to docker and hooks; nothing else leaks in,
# so compose interpolation sees only the runtime file and the AF_* release values.
PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
)


class DeployError(Exception):
    """Aborts the current command with a message and an exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def log(message: str) -> None:
    print(f"release_host: {message}", flush=True)


def warn(message: str) -> None:
    print(f"release_host: WARNING: {message}", file=sys.stderr, flush=True)


def utc_now_text(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Injectable dependencies (tests replace docker, HTTP, clock, and user ids)


def run_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    capture: bool = True,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Run argv without a shell; captured output is text, streamed output goes to the log."""
    try:
        return subprocess.run(
            argv,
            env=env,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise DeployError(f"command not found: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise DeployError(f"command timed out after {timeout:.0f}s: {' '.join(argv[:3])}") from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 - urllib signature
        return None


def fetch_url(url: str, timeout: float) -> tuple[int, bytes]:
    """GET a loopback URL without proxies or redirects; returns (status, body)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "af-release-host"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, b""


@dataclass
class Deps:
    run: Callable[..., subprocess.CompletedProcess] = run_command
    fetch: Callable[[str, float], tuple[int, bytes]] = fetch_url
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    euid: int = field(default_factory=os.geteuid)
    egid: int = field(default_factory=os.getegid)
    stdin_wait: Callable[[], None] = lambda: sys.stdin.buffer.read()


# ---------------------------------------------------------------------------
# Ownership and permissions


@dataclass(frozen=True)
class Owner:
    mode: str
    uid: int
    gid: int

    @property
    def dir_mode(self) -> int:
        return 0o750 if self.mode == "root" else 0o700

    @staticmethod
    def for_mode(mode: str, deps: Deps) -> Owner:
        if mode == "root":
            if deps.euid != 0:
                raise DeployError(
                    "--owner root must run as root (use the sudoers rule for the installed tool)", EXIT_INVALID
                )
            return Owner("root", 0, 0)
        if mode == "self":
            if deps.euid == 0:
                raise DeployError("--owner self must run as the non-root deploy user, not root", EXIT_INVALID)
            return Owner("self", deps.euid, deps.egid)
        raise DeployError(f"unknown owner mode: {mode}", EXIT_INVALID)

    def check_path(self, path: Path, *, kind: str) -> os.stat_result:
        """The path exists, is not a symlink, is owned by us, and is not group/other writable."""
        try:
            info = os.lstat(path)
        except FileNotFoundError as error:
            raise DeployError(f"{path} does not exist", EXIT_INVALID) from error
        if stat.S_ISLNK(info.st_mode):
            raise DeployError(f"{path} is a symlink. Refusing to follow it", EXIT_INVALID)
        if kind == "dir" and not stat.S_ISDIR(info.st_mode):
            raise DeployError(f"{path} is not a directory", EXIT_INVALID)
        if kind == "file" and not stat.S_ISREG(info.st_mode):
            raise DeployError(f"{path} is not a regular file", EXIT_INVALID)
        if info.st_uid != self.uid:
            raise DeployError(
                f"{path} is owned by uid {info.st_uid}, expected {self.uid} (--owner {self.mode})", EXIT_INVALID
            )
        if info.st_mode & 0o022:
            raise DeployError(f"{path} is group- or world-writable (mode {stat.S_IMODE(info.st_mode):o})", EXIT_INVALID)
        return info

    def check_ancestors(self, path: Path) -> None:
        """The path and its ancestors are real directories owned by root or us, and not
        writable by others unless root-owned and sticky (such as /tmp)."""
        if Path(os.path.realpath(path)) != path:
            raise DeployError(
                f"{path} must be an absolute path without symlinks (resolved: {os.path.realpath(path)})", EXIT_INVALID
            )
        for ancestor in [*reversed(path.parents), path]:
            info = os.lstat(ancestor)
            if not stat.S_ISDIR(info.st_mode):
                raise DeployError(f"{ancestor} is not a directory", EXIT_INVALID)
            if info.st_uid not in (0, self.uid):
                raise DeployError(
                    f"{ancestor} is owned by uid {info.st_uid}; ancestors must be owned by root or the deploy owner",
                    EXIT_INVALID,
                )
            sticky_root_dir = info.st_mode & stat.S_ISVTX and info.st_uid == 0
            if info.st_mode & 0o022 and not sticky_root_dir:
                raise DeployError(f"{ancestor} is group- or world-writable", EXIT_INVALID)

    def ensure_dir(self, path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            os.mkdir(path, self.dir_mode)
            os.chmod(path, self.dir_mode)
        self.check_path(path, kind="dir")

    def write_file(self, path: Path, data: bytes, mode: int = 0o600) -> None:
        """Atomically replace path with data, owner-only, never following symlinks."""
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, mode)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, path)


# ---------------------------------------------------------------------------
# Host layout


@dataclass(frozen=True)
class Layout:
    base: Path

    @property
    def releases(self) -> Path:
        return self.base / "releases"

    @property
    def current(self) -> Path:
        return self.base / "current"

    @property
    def incoming(self) -> Path:
        return self.base / "incoming"

    @property
    def shared(self) -> Path:
        return self.base / "shared"

    @property
    def backups(self) -> Path:
        return self.base / "backups"

    @property
    def lock_file(self) -> Path:
        return self.base / "deploy.lock"

    @property
    def lock_owner(self) -> Path:
        return self.base / "deploy.lock.owner"

    @property
    def environment_marker(self) -> Path:
        return self.base / "environment"

    @property
    def host_generated(self) -> Path:
        return self.shared / "host-generated.env"

    @property
    def record(self) -> Path:
        return self.base / "deployment.json"

    @property
    def history(self) -> Path:
        return self.base / "deployments.jsonl"

    def release_dir(self, sha: str) -> Path:
        return self.releases / sha

    def source_dir(self, sha: str) -> Path:
        return self.release_dir(sha) / "source"

    def release_meta(self, sha: str) -> Path:
        return self.release_dir(sha) / "release.json"

    def runtime(self, sha: str) -> Path:
        return self.release_dir(sha) / "runtime.env"

    def runtime_next(self, sha: str) -> Path:
        return self.release_dir(sha) / "runtime.env.next"


def require_sha(value: str) -> str:
    if not SHA_PATTERN.match(value or ""):
        raise DeployError("--sha must be a full 40-character lowercase commit SHA", EXIT_INVALID)
    return value


def require_environment(value: str) -> str:
    if not ENVIRONMENT_PATTERN.match(value or ""):
        raise DeployError("--environment must match [a-z][a-z0-9-]{0,31}", EXIT_INVALID)
    return value


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise DeployError(f"{path} is not valid JSON: {error}", EXIT_INVALID) from error


def write_json(owner: Owner, path: Path, data: dict) -> None:
    owner.write_file(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def current_sha(layout: Layout) -> str | None:
    if not layout.current.is_symlink():
        if layout.current.exists():
            raise DeployError(f"{layout.current} exists but is not a symlink", EXIT_INVALID)
        return None
    target = os.readlink(layout.current)
    parts = Path(target).parts
    if len(parts) != 2 or parts[0] != "releases" or not SHA_PATTERN.match(parts[1]):
        raise DeployError(f"{layout.current} points at an unexpected target: {target}", EXIT_INVALID)
    return parts[1]


def release_shas(layout: Layout) -> list[str]:
    if not layout.releases.is_dir():
        return []
    return sorted(entry.name for entry in layout.releases.iterdir() if SHA_PATTERN.match(entry.name))


# ---------------------------------------------------------------------------
# Project release configuration (deploy/release.toml in the commit)


@dataclass(frozen=True)
class ImageSpec:
    name: str
    dockerfile: str
    context: str


@dataclass(frozen=True)
class EnvironmentSpec:
    local_url: str
    keep: int


@dataclass(frozen=True)
class ReleaseConfig:
    app: str
    compose_file: str
    service: str
    health_path: str
    revision_key: str
    candidate_port: int
    candidate: bool
    preflight: tuple[str, ...]
    migrate: tuple[str, ...]
    migrate_service: str
    wait_timeout: int
    health_attempts: int
    health_interval: float
    images: tuple[ImageSpec, ...]
    environments: dict[str, EnvironmentSpec]

    def environment(self, name: str) -> EnvironmentSpec:
        if name not in self.environments:
            raise DeployError(f"deploy/release.toml has no [environments.{name}] section", EXIT_INVALID)
        return self.environments[name]

    def project(self, environment: str) -> str:
        return f"{self.app}-{environment}"

    def image_tag(self, environment: str, image: ImageSpec, sha: str) -> str:
        return f"{self.app}-{environment}/{image.name}:{sha}"

    def image_variable(self, image: ImageSpec) -> str:
        return "AF_IMAGE_" + image.name.upper().replace("-", "_")


RELEASE_KEYS = {
    "app",
    "compose_file",
    "service",
    "health_path",
    "revision_key",
    "candidate_port",
    "candidate",
    "preflight",
    "migrate",
    "migrate_service",
    "wait_timeout",
    "health_attempts",
    "health_interval",
    "images",
    "environments",
}
REQUIRED_RELEASE_KEYS = {
    "app",
    "compose_file",
    "service",
    "health_path",
    "candidate_port",
    "preflight",
    "migrate",
    "images",
    "environments",
}


def _relative_path(value: object, key: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise DeployError(f"release.toml: {key} must be a relative path", EXIT_INVALID)
    if any(part in ("..", "") for part in value.split("/")) and value != ".":
        raise DeployError(f"release.toml: {key} must not contain '..' or empty segments", EXIT_INVALID)
    return value


def _argv(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise DeployError(f"release.toml: {key} must be a list of non-empty strings ([] to disable)", EXIT_INVALID)
    return tuple(value)


def _positive_int(value: object, key: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise DeployError(f"release.toml: {key} must be an integer from 1 to {maximum}", EXIT_INVALID)
    return value


def load_release_config(source: Path) -> ReleaseConfig:
    path = source / "deploy" / "release.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DeployError("the commit has no deploy/release.toml", EXIT_INVALID) from error
    except tomllib.TOMLDecodeError as error:
        raise DeployError(f"deploy/release.toml is not valid TOML: {error}", EXIT_INVALID) from error
    unknown = set(data) - RELEASE_KEYS
    if unknown:
        raise DeployError(f"release.toml: unknown keys {sorted(unknown)}", EXIT_INVALID)
    missing = REQUIRED_RELEASE_KEYS - set(data)
    if missing:
        raise DeployError(f"release.toml: missing keys {sorted(missing)}", EXIT_INVALID)
    app = data["app"]
    if not isinstance(app, str) or not APP_PATTERN.match(app):
        raise DeployError("release.toml: app must match [a-z0-9][a-z0-9-]{0,39}", EXIT_INVALID)
    service = data["service"]
    migrate_service = data.get("migrate_service", service)
    for key, value in (("service", service), ("migrate_service", migrate_service)):
        if not isinstance(value, str) or not SERVICE_PATTERN.match(value):
            raise DeployError(f"release.toml: {key} is not a valid compose service name", EXIT_INVALID)
    health_path = data["health_path"]
    if not isinstance(health_path, str) or not re.match(r"^/[A-Za-z0-9._~/-]*$", health_path):
        raise DeployError("release.toml: health_path must be an absolute URL path", EXIT_INVALID)
    revision_key = data.get("revision_key", "revision")
    if not isinstance(revision_key, str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", revision_key):
        raise DeployError("release.toml: revision_key must be a simple JSON key", EXIT_INVALID)
    candidate = data.get("candidate", True)
    if not isinstance(candidate, bool):
        raise DeployError("release.toml: candidate must be true or false", EXIT_INVALID)
    health_interval = data.get("health_interval", 2)
    if (
        not isinstance(health_interval, (int, float))
        or isinstance(health_interval, bool)
        or not 0 < health_interval <= 60
    ):
        raise DeployError("release.toml: health_interval must be a number of seconds from 0 to 60", EXIT_INVALID)

    raw_images = data["images"]
    if not isinstance(raw_images, list) or not raw_images:
        raise DeployError("release.toml: images must be a non-empty array of tables ([[images]])", EXIT_INVALID)
    images = []
    for index, raw in enumerate(raw_images):
        if not isinstance(raw, dict) or set(raw) - {"name", "dockerfile", "context"} or "name" not in raw:
            raise DeployError(f"release.toml: images[{index}] takes name, dockerfile, context", EXIT_INVALID)
        name = raw["name"]
        if not isinstance(name, str) or not IMAGE_NAME_PATTERN.match(name):
            raise DeployError(f"release.toml: images[{index}].name must match [a-z0-9][a-z0-9-]*", EXIT_INVALID)
        images.append(
            ImageSpec(
                name=name,
                dockerfile=_relative_path(raw.get("dockerfile", "Dockerfile"), f"images[{index}].dockerfile"),
                context=_relative_path(raw.get("context", "."), f"images[{index}].context"),
            )
        )
    if len({image.name for image in images}) != len(images):
        raise DeployError("release.toml: image names must be unique", EXIT_INVALID)

    raw_environments = data["environments"]
    if not isinstance(raw_environments, dict) or not raw_environments:
        raise DeployError("release.toml: environments must define at least one [environments.<name>]", EXIT_INVALID)
    environments = {}
    for name, raw in raw_environments.items():
        require_environment(name)
        if not isinstance(raw, dict) or set(raw) - {"local_url", "keep"} or "local_url" not in raw:
            raise DeployError(f"release.toml: [environments.{name}] takes local_url and keep", EXIT_INVALID)
        local_url = raw["local_url"]
        if not isinstance(local_url, str) or not LOCAL_URL_PATTERN.match(local_url):
            raise DeployError(
                f"release.toml: environments.{name}.local_url must be http://127.0.0.1:<port> (loopback)", EXIT_INVALID
            )
        keep = _positive_int(raw.get("keep", DEFAULT_KEEP.get(name, FALLBACK_KEEP)), f"environments.{name}.keep", 50)
        environments[name] = EnvironmentSpec(local_url=local_url, keep=keep)

    return ReleaseConfig(
        app=app,
        compose_file=_relative_path(data["compose_file"], "compose_file"),
        service=service,
        health_path=health_path,
        revision_key=revision_key,
        candidate_port=_positive_int(data["candidate_port"], "candidate_port", 65535),
        candidate=candidate,
        preflight=_argv(data["preflight"], "preflight"),
        migrate=_argv(data["migrate"], "migrate"),
        migrate_service=migrate_service,
        wait_timeout=_positive_int(data.get("wait_timeout", 180), "wait_timeout", 3600),
        health_attempts=_positive_int(data.get("health_attempts", 30), "health_attempts", 600),
        health_interval=float(health_interval),
        images=tuple(images),
        environments=environments,
    )


# ---------------------------------------------------------------------------
# Safe extraction and tree digest


def _member_parts(name: str) -> list[str]:
    if "\x00" in name or "\\" in name:
        raise DeployError(f"archive member has an unsafe name: {name!r}", EXIT_INVALID)
    if name.startswith("/"):
        raise DeployError(f"archive member has an absolute path: {name}", EXIT_INVALID)
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise DeployError(f"archive member escapes the release: {name}", EXIT_INVALID)
    return parts


def _is_env_file(parts: list[str]) -> bool:
    return any(part.startswith(".env") for part in parts)


def _real_parent(root: Path, parts: list[str]) -> Path:
    """The parent directory of parts under root, refusing any symlinked component."""
    current = root
    for part in parts[:-1]:
        current = current / part
        info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise DeployError(f"archive path passes through a non-directory: {'/'.join(parts)}", EXIT_INVALID)
    return current


def safe_extract(archive: Path, destination: Path, max_bytes: int) -> int:
    """Extract a tar archive into the empty destination; returns skipped .env* members.

    Only directories, regular files, and symlinks whose target stays inside the release
    are allowed. Files keep only their executable bit; .env* files are never extracted.
    """
    skipped = 0
    total = 0
    seen: set[str] = set()
    try:
        tar = tarfile.open(archive, mode="r:*")
    except tarfile.TarError as error:
        raise DeployError(f"archive is not a readable tar file: {error}", EXIT_INVALID) from error
    with tar:
        members = tar.getmembers()
        if len(members) > MAX_MEMBERS:
            raise DeployError(f"archive has more than {MAX_MEMBERS} members", EXIT_INVALID)
        for member in members:
            parts = _member_parts(member.name)
            if not parts:
                continue
            relative = "/".join(parts)
            if relative in seen:
                raise DeployError(f"archive repeats member {relative}", EXIT_INVALID)
            seen.add(relative)
            if _is_env_file(parts):
                skipped += 1
                continue
            parent = _real_parent(destination, parts)
            target = parent / parts[-1]
            if member.isdir():
                os.mkdir(target, 0o700)
            elif member.isreg():
                total += member.size
                if total > max_bytes:
                    raise DeployError(f"archive expands beyond {max_bytes} bytes", EXIT_INVALID)
                mode = 0o700 if member.mode & 0o111 else 0o600
                source = tar.extractfile(member)
                if source is None:
                    raise DeployError(f"cannot read archive member {relative}", EXIT_INVALID)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                with source, os.fdopen(os.open(target, flags, mode), "wb") as handle:
                    shutil.copyfileobj(source, handle)
            elif member.issym():
                link = member.linkname
                if link.startswith("/") or "\x00" in link:
                    raise DeployError(f"symlink {relative} has an absolute target", EXIT_INVALID)
                resolved = os.path.normpath(os.path.join(os.path.dirname(relative), link))
                if resolved == ".." or resolved.startswith("../") or os.path.isabs(resolved):
                    raise DeployError(f"symlink {relative} points outside the release", EXIT_INVALID)
                os.symlink(link, target)
            else:
                raise DeployError(
                    f"archive member {relative} has an unsupported type (only files, directories, symlinks)",
                    EXIT_INVALID,
                )
    return skipped


def tree_digest(root: Path) -> str:
    """sha256 over sorted (type, exec bit, path, content or link target) entries."""
    digest = hashlib.sha256()
    entries = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            path = Path(directory) / name
            entries.append(path.relative_to(root).as_posix())
    for relative in sorted(entries):
        path = root / relative
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            digest.update(f"l {relative} {os.readlink(path)}\n".encode("utf-8"))
        elif stat.S_ISDIR(info.st_mode):
            digest.update(f"d {relative}\n".encode("utf-8"))
        elif stat.S_ISREG(info.st_mode):
            content = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    content.update(chunk)
            executable = "x" if info.st_mode & 0o100 else "-"
            digest.update(f"f {executable} {relative} {content.hexdigest()}\n".encode("utf-8"))
        else:
            raise DeployError(f"release tree contains an unsupported file type: {relative}", EXIT_INVALID)
    return digest.hexdigest()


def make_read_only(root: Path) -> None:
    for directory, _dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chmod(path, 0o500 if os.lstat(path).st_mode & 0o100 else 0o400)
        os.chmod(directory, 0o500)


def remove_tree(path: Path) -> None:
    """Remove a release tree that make_read_only protected."""
    for directory, _dirnames, _filenames in os.walk(path):
        os.chmod(directory, 0o700)
    shutil.rmtree(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Runtime files


def parse_runtime(text: str, label: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse a materialized runtime file; values are never included in errors."""
    entries: list[tuple[str, str]] = []
    host_generated: list[str] = []
    names: set[str] = set()
    for number, line in enumerate(text.split("\n"), start=1):
        if line.startswith(HOST_GENERATED_HEADER + " "):
            host_generated = line[len(HOST_GENERATED_HEADER) + 1 :].split()
            if not all(VARIABLE_PATTERN.match(name) for name in host_generated):
                raise DeployError(f"{label}: line {number}: invalid host-generated name", EXIT_INVALID)
            continue
        if not line or line.startswith("#"):
            continue
        match = RUNTIME_LINE.match(line)
        if match is None:
            raise DeployError(f"{label}: line {number} is not NAME='value' (value not shown)", EXIT_INVALID)
        name = match.group(1)
        if name in names:
            raise DeployError(f"{label}: {name} is defined twice", EXIT_INVALID)
        names.add(name)
        entries.append((name, match.group(2)))
    clash = names & set(host_generated)
    if clash:
        raise DeployError(f"{label}: host-generated names must not be supplied: {sorted(clash)}", EXIT_INVALID)
    return entries, host_generated


def render_runtime(entries: list[tuple[str, str]]) -> bytes:
    lines = ["# Runtime for one release; owner-only. Values are never printed."]
    lines += [f"{name}='{value}'" for name, value in entries]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# The host context


class Host:
    def __init__(self, base: str, owner_mode: str, deps: Deps) -> None:
        if not base or not base.startswith("/"):
            raise DeployError("--base must be an absolute path", EXIT_INVALID)
        self.deps = deps
        self.owner = Owner.for_mode(owner_mode, deps)
        self.layout = Layout(Path(base))
        if self.layout.base.parent == self.layout.base:
            raise DeployError("--base must not be the filesystem root", EXIT_INVALID)

    # -- structure ---------------------------------------------------------

    def ensure_structure(self) -> None:
        layout = self.layout
        if not layout.base.parent.is_dir():
            raise DeployError(f"{layout.base.parent} does not exist. Create the deploy root first", EXIT_INVALID)
        self.owner.check_ancestors(layout.base.parent)
        self.owner.ensure_dir(layout.base)
        for directory in (layout.releases, layout.incoming, layout.shared, layout.backups):
            self.owner.ensure_dir(directory)

    def check_environment(self, environment: str) -> None:
        marker = self.layout.environment_marker
        if marker.exists():
            self.owner.check_path(marker, kind="file")
            recorded = marker.read_text(encoding="utf-8").strip()
            if recorded != environment:
                raise DeployError(
                    f"{self.layout.base} belongs to environment '{recorded}', not '{environment}'", EXIT_INVALID
                )
        else:
            self.owner.write_file(marker, (environment + "\n").encode("utf-8"), 0o640)

    # -- lock ----------------------------------------------------------------

    def _open_lock(self) -> int:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.layout.lock_file, flags, 0o600)
        self.owner.check_path(self.layout.lock_file, kind="file")
        return descriptor

    def acquire_lock(self, wait_seconds: int, token: str) -> int:
        self.ensure_structure()
        descriptor = self._open_lock()
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    os.close(descriptor)
                    raise
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    holder = read_json(self.layout.lock_owner)
                    since = holder.get("acquired_at", "unknown time")
                    raise DeployError(
                        f"another deployment holds the lock for {self.layout.base} (since {since}). "
                        f"Waited {wait_seconds}s",
                        EXIT_LOCK_BUSY,
                    ) from None
                self.deps.sleep(1)
        owner_record = {
            "acquired_at": utc_now_text(self.deps.now()),
            "pid": os.getpid(),
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        }
        write_json(self.owner, self.layout.lock_owner, owner_record)
        return descriptor

    def release_lock(self, descriptor: int) -> None:
        self.layout.lock_owner.unlink(missing_ok=True)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def require_lock(self, token: str | None) -> None:
        """Mutating commands run only while the caller's lock is actually held."""
        if not token or not TOKEN_PATTERN.match(token):
            raise DeployError("this command needs --lock-token (or AF_LOCK_TOKEN) from a held lock", EXIT_INVALID)
        holder = read_json(self.layout.lock_owner)
        expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if holder.get("token_sha256") != expected:
            raise DeployError("the lock is not held with this token", EXIT_INVALID)
        descriptor = self._open_lock()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        finally:
            os.close(descriptor)
        raise DeployError("the lock owner record exists but nobody holds the lock", EXIT_INVALID)

    # -- docker helpers ------------------------------------------------------

    def environ(self, extra: dict[str, str]) -> dict[str, str]:
        env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
        env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        # Under sudo HOME may still name the deploy user's home; docker must not write
        # root-owned files there.
        env["HOME"] = pwd.getpwuid(self.deps.euid).pw_dir if self.owner.mode == "root" else env.get("HOME", "/")
        env.update(extra)
        return env

    def docker(
        self, *args: str, capture: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        return self.deps.run(["docker", *args], env=env or self.environ({}), capture=capture)

    def release_env(self, config: ReleaseConfig, environment: str, sha: str, runtime: Path) -> dict[str, str]:
        values = {
            "AF_ENVIRONMENT": environment,
            "AF_RELEASE_SHA": sha,
            "AF_BASE": str(self.layout.base),
            "AF_RELEASE_DIR": str(self.layout.release_dir(sha)),
            "AF_SOURCE_DIR": str(self.layout.source_dir(sha)),
            "AF_RUNTIME_FILE": str(runtime),
            "AF_COMPOSE_PROJECT": config.project(environment),
        }
        for image in config.images:
            values[config.image_variable(image)] = config.image_tag(environment, image, sha)
        return values

    def compose_argv(
        self, config: ReleaseConfig, environment: str, sha: str, runtime: Path, project: str | None = None
    ) -> list[str]:
        source = self.layout.source_dir(sha)
        return [
            "docker",
            "compose",
            "--project-name",
            project or config.project(environment),
            "--project-directory",
            str(source),
            "--file",
            str(source / config.compose_file),
            "--env-file",
            str(runtime),
        ]

    def compose(
        self,
        config: ReleaseConfig,
        environment: str,
        sha: str,
        runtime: Path,
        *args: str,
        project: str | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        argv = self.compose_argv(config, environment, sha, runtime, project) + list(args)
        env = self.environ(self.release_env(config, environment, sha, runtime))
        return self.deps.run(argv, env=env, cwd=self.layout.source_dir(sha), capture=capture)

    def inspect_image(self, tag: str) -> dict | None:
        result = self.docker("image", "inspect", tag)
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DeployError(f"docker image inspect returned invalid JSON for {tag}") from error
        if not data:
            return None
        return data[0]

    def running_revisions(self, app: str, project: str | None = None) -> list[str]:
        """Revisions of running containers built by this tool, from the OCI revision label."""
        args = ["ps", "--filter", f"label={APP_LABEL}={app}"]
        if project is not None:
            args += ["--filter", f"label=com.docker.compose.project={project}"]
        args += ["--format", '{{.Label "' + REVISION_LABEL + '"}}']
        result = self.docker(*args)
        if result.returncode != 0:
            raise DeployError("docker ps failed. Cannot tell which releases are running")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # -- hooks ---------------------------------------------------------------

    def run_hook(
        self,
        name: str,
        config: ReleaseConfig,
        environment: str,
        sha: str,
        runtime: Path,
        extra: dict[str, str] | None = None,
    ) -> bool:
        if name not in HOOK_NAMES:
            raise DeployError(f"unknown hook {name}", EXIT_INVALID)
        hook = self.layout.source_dir(sha) / "deploy" / "hooks" / name
        if not os.path.lexists(hook):
            return False
        info = os.lstat(hook)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o100:
            raise DeployError(
                f"hook deploy/hooks/{name} must be an executable regular file (not a symlink)", EXIT_INVALID
            )
        log(f"hook {name}: running")
        values = self.release_env(config, environment, sha, runtime)
        values.update(extra or {})
        result = self.deps.run(
            [str(hook)],
            env=self.environ(values),
            cwd=self.layout.source_dir(sha),
            capture=False,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise DeployError(f"hook {name} failed with exit {result.returncode}")
        log(f"hook {name}: ok")
        return True

    # -- health ----------------------------------------------------------------

    def check_revision(self, url: str, sha: str, config: ReleaseConfig, label: str) -> None:
        observed = "no response"
        for attempt in range(1, config.health_attempts + 1):
            try:
                status, body = self.deps.fetch(url, 5.0)
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
                observed = f"connection error ({type(error).__name__})"
            else:
                if status == 200:
                    try:
                        revision = json.loads(body.decode("utf-8")).get(config.revision_key)
                    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                        revision = None
                    if revision == sha:
                        log(f"{label}: {url} reports revision {sha[:12]} (attempt {attempt})")
                        return
                    observed = f"HTTP 200 with revision {str(revision)[:40]!r}"
                else:
                    observed = f"HTTP {status}"
            if attempt < config.health_attempts:
                self.deps.sleep(config.health_interval)
        raise DeployError(
            f"{label}: {url} did not report revision {sha[:12]} after {config.health_attempts} attempts "
            f"(last: {observed})"
        )

    def load_config(self, sha: str) -> ReleaseConfig:
        source = self.layout.source_dir(sha)
        if not source.is_dir():
            raise DeployError(f"release {sha[:12]} is not prepared on this host", EXIT_INVALID)
        return load_release_config(source)


# ---------------------------------------------------------------------------
# Commands


def command_lock(host: Host, args: argparse.Namespace) -> int:
    if args.hold and args.command:
        raise DeployError("use either --hold or -- COMMAND, not both", EXIT_INVALID)
    if args.hold:
        token = args.token or ""
        if not TOKEN_PATTERN.match(token):
            raise DeployError("--hold needs --token of 16-128 characters [A-Za-z0-9_-]", EXIT_INVALID)
        descriptor = host.acquire_lock(args.wait, token)
        try:
            print("AF-LOCK-ACQUIRED", flush=True)
            host.deps.stdin_wait()
        finally:
            host.release_lock(descriptor)
        log("lock released")
        return EXIT_OK
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise DeployError("lock needs --hold --token T or -- COMMAND...", EXIT_INVALID)
    token = secrets.token_urlsafe(24)
    descriptor = host.acquire_lock(args.wait, token)
    try:
        env = dict(os.environ)
        env["AF_LOCK_TOKEN"] = token
        result = subprocess.run(command, env=env, check=False)
        return result.returncode
    finally:
        host.release_lock(descriptor)


def command_secure_file(host: Host, args: argparse.Namespace) -> int:
    host.require_lock(args.lock_token)
    source = Path(args.path)
    if not source.is_absolute() or not FILE_NAME_PATTERN.match(source.name):
        raise DeployError("secure-file needs an absolute path with a simple file name", EXIT_INVALID)
    info = os.lstat(source)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DeployError(f"{source} must be a regular file with a single link", EXIT_INVALID)
    if info.st_uid not in (host.deps.euid, 0) and host.owner.mode == "self":
        raise DeployError(f"{source} is not owned by the deploy user", EXIT_INVALID)
    parent = os.lstat(source.parent)
    if parent.st_mode & 0o077 or stat.S_ISLNK(parent.st_mode):
        raise DeployError(f"{source.parent} must be a private (0700) upload directory", EXIT_INVALID)
    host.ensure_structure()
    destination = host.layout.incoming / source.name
    with open(source, "rb") as handle:
        os.fstat(handle.fileno())
        data = handle.read()
    host.owner.write_file(destination, data, 0o600)
    source.unlink()
    host.owner.check_path(destination, kind="file")
    print(destination, flush=True)
    return EXIT_OK


def merge_host_generated(host: Host, entries: list[tuple[str, str]], names: list[str]) -> list[tuple[str, str]]:
    """Add host-generated secrets (created once on this host, never sent back to CI)."""
    if not names:
        return entries
    store_path = host.layout.host_generated
    stored: dict[str, str] = {}
    if store_path.exists():
        host.owner.check_path(store_path, kind="file")
        if stat.S_IMODE(os.lstat(store_path).st_mode) & 0o077:
            raise DeployError(f"{store_path} must be mode 0600", EXIT_INVALID)
        stored_entries, _ = parse_runtime(store_path.read_text(encoding="utf-8"), str(store_path))
        stored = dict(stored_entries)
    created = [name for name in names if name not in stored]
    for name in created:
        stored[name] = secrets.token_urlsafe(32)
    if created:
        host.owner.write_file(store_path, render_runtime(sorted(stored.items())), 0o600)
        log(f"generated host secrets: {', '.join(created)} (values stay on this host)")
    return entries + [(name, stored[name]) for name in names]


def command_prepare(host: Host, args: argparse.Namespace) -> int:
    sha = require_sha(args.sha)
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    host.ensure_structure()
    host.check_environment(environment)
    layout = host.layout
    archive = Path(args.archive)
    runtime_upload = Path(args.runtime)
    for path in (archive, runtime_upload):
        if path.parent != layout.incoming:
            raise DeployError(f"{path} must be in {layout.incoming} (use secure-file first)", EXIT_INVALID)
        host.owner.check_path(path, kind="file")

    archive_sha256 = file_sha256(archive)
    staging = layout.releases / f".incoming-{sha}-{secrets.token_hex(4)}"
    os.mkdir(staging, 0o700)
    try:
        os.mkdir(staging / "source", 0o700)
        skipped = safe_extract(archive, staging / "source", args.max_bytes)
        if skipped:
            log(f"skipped {skipped} .env* file(s) from the archive. Runtime comes from the runtime file only")
        digest = tree_digest(staging / "source")
        release_dir = layout.release_dir(sha)
        if os.path.lexists(release_dir):
            host.owner.check_path(release_dir, kind="dir")
            meta = read_json(layout.release_meta(sha))
            recorded = meta.get("tree_sha256")
            if not recorded:
                raise DeployError(
                    f"release {sha[:12]} exists without a recorded digest. Refusing to reuse it", EXIT_INVALID
                )
            on_disk = tree_digest(layout.source_dir(sha))
            if on_disk != recorded:
                raise DeployError(
                    f"release {sha[:12]} was modified on disk (tree digest mismatch). Investigate before deploying",
                    EXIT_INVALID,
                )
            if digest != recorded:
                raise DeployError(
                    f"release {sha[:12]} already exists with different content. Releases are immutable", EXIT_INVALID
                )
            log(f"release {sha[:12]} already prepared. Content verified (tree {digest[:12]})")
        else:
            make_read_only(staging / "source")
            meta = {
                "sha": sha,
                "environment": environment,
                "archive_sha256": archive_sha256,
                "tree_sha256": digest,
                "prepared_at": utc_now_text(host.deps.now()),
                "images": {},
            }
            write_json(host.owner, staging / "release.json", meta)
            os.chmod(staging, host.owner.dir_mode)
            os.rename(staging, release_dir)
            log(f"release {sha[:12]} prepared (tree {digest[:12]}, archive {archive_sha256[:12]})")
    finally:
        if staging.exists():
            remove_tree(staging)

    entries, host_names = parse_runtime(runtime_upload.read_text(encoding="utf-8"), "runtime file")
    merged = merge_host_generated(host, entries, host_names)
    host.owner.write_file(layout.runtime_next(sha), render_runtime(merged), 0o600)
    archive.unlink()
    runtime_upload.unlink()
    log(f"runtime installed for {sha[:12]} ({len(merged)} variables)")
    return EXIT_OK


def ensure_images(host: Host, config: ReleaseConfig, environment: str, sha: str) -> dict[str, str]:
    """Build each image once per commit with the revision label; verify recorded ids."""
    layout = host.layout
    meta = read_json(layout.release_meta(sha))
    recorded: dict[str, str] = dict(meta.get("images", {}))
    tags: dict[str, str] = dict(meta.get("image_tags", {}))
    source = layout.source_dir(sha)
    for image in config.images:
        tag = config.image_tag(environment, image, sha)
        details = host.inspect_image(tag)
        if details is None:
            log(f"building {tag}")
            result = host.docker(
                "build",
                "--file",
                str(source / image.dockerfile),
                "--tag",
                tag,
                "--label",
                f"{REVISION_LABEL}={sha}",
                "--label",
                f"{APP_LABEL}={config.app}",
                "--build-arg",
                f"AF_REVISION={sha}",
                str(source / image.context),
                capture=False,
            )
            if result.returncode != 0:
                raise DeployError(f"image build failed for {tag}")
            details = host.inspect_image(tag)
            if details is None:
                raise DeployError(f"image {tag} is missing after the build")
            if image.name in recorded and recorded[image.name] != details.get("Id"):
                warn(f"{tag} was rebuilt. Its image id changed from the recorded one")
            recorded[image.name] = details.get("Id", "")
        labels = (details.get("Config") or {}).get("Labels") or {}
        if labels.get(REVISION_LABEL) != sha or labels.get(APP_LABEL) != config.app:
            raise DeployError(f"image {tag} does not carry the expected revision and app labels")
        if recorded.get(image.name) not in (None, details.get("Id")):
            raise DeployError(f"image {tag} id differs from the id recorded at build time. Refusing to run it")
        recorded[image.name] = details.get("Id", "")
        tags[image.name] = tag
    meta["images"] = recorded
    meta["image_tags"] = tags
    write_json(host.owner, layout.release_meta(sha), meta)
    return recorded


def candidate_check(host: Host, config: ReleaseConfig, environment: str, sha: str, runtime: Path) -> None:
    """Start the service from the new image in its own compose project on an ephemeral
    loopback port, without dependencies or the live ports, and check its revision."""
    project = f"{config.project(environment)}-candidate"
    name = f"{project}-{sha[:12]}"
    host.docker("rm", "--force", "--volumes", name)
    try:
        result = host.compose(
            config,
            environment,
            sha,
            runtime,
            "run",
            "--detach",
            "--no-deps",
            "--name",
            name,
            "--publish",
            f"127.0.0.1::{config.candidate_port}",
            config.service,
            project=project,
            capture=True,
        )
        if result.returncode != 0:
            raise DeployError(f"candidate container did not start (exit {result.returncode})")
        port_result = host.docker("port", name, f"{config.candidate_port}/tcp")
        address = (
            port_result.stdout.strip().splitlines()[0]
            if port_result.returncode == 0 and port_result.stdout.strip()
            else ""
        )
        match = re.match(r"^127\.0\.0\.1:(\d+)$", address)
        if match is None:
            raise DeployError("candidate container has no loopback port mapping")
        host.check_revision(f"http://127.0.0.1:{match.group(1)}{config.health_path}", sha, config, "candidate")
    finally:
        host.docker("rm", "--force", "--volumes", name)
        host.compose(
            config, environment, sha, runtime, "down", "--volumes", "--remove-orphans", project=project, capture=True
        )


def run_backup(host: Host, config: ReleaseConfig, environment: str, sha: str, runtime: Path) -> str | None:
    hook = host.layout.source_dir(sha) / "deploy" / "hooks" / "backup"
    if not os.path.lexists(hook):
        return None
    stamp = host.deps.now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = host.layout.backups / f"{stamp}-{sha[:12]}"
    host.owner.ensure_dir(backup_dir)
    host.run_hook("backup", config, environment, sha, runtime, {"AF_BACKUP_DIR": str(backup_dir)})
    if not any(backup_dir.iterdir()):
        raise DeployError("backup hook wrote nothing into AF_BACKUP_DIR. Refusing to migrate")
    log(f"backup stored in {backup_dir}")
    return str(backup_dir)


def start_release(host: Host, config: ReleaseConfig, environment: str, sha: str, runtime: Path) -> None:
    result = host.compose(
        config,
        environment,
        sha,
        runtime,
        "up",
        "--detach",
        "--no-build",
        "--wait",
        "--wait-timeout",
        str(config.wait_timeout),
        "--remove-orphans",
    )
    if result.returncode != 0:
        raise DeployError(f"docker compose up failed for {sha[:12]} (exit {result.returncode})")
    local = config.environment(environment).local_url + config.health_path
    host.check_revision(local, sha, config, "local health")


def restore_previous(host: Host, environment: str, failed_sha: str, previous: str | None) -> int:
    """Bring the previous release back after a failed start; returns the exit code."""
    if previous is None:
        warn("no previous release to restore. Stopping the failed release")
        try:
            config = host.load_config(failed_sha)
            runtime = host.layout.runtime_next(failed_sha)
            if not runtime.exists():
                runtime = host.layout.runtime(failed_sha)
            host.compose(config, environment, failed_sha, runtime, "down", "--remove-orphans")
        except DeployError as error:
            warn(f"could not stop the failed release: {error}")
        return EXIT_FAILED
    log(f"restoring previous release {previous[:12]}")
    try:
        config = host.load_config(previous)
        runtime = host.layout.runtime(previous)
        host.owner.check_path(runtime, kind="file")
        ensure_images(host, config, environment, previous)
        start_release(host, config, environment, previous, runtime)
    except DeployError as error:
        warn(f"RESTORE FAILED: {error}")
        return EXIT_RESTORE_FAILED
    log(f"previous release {previous[:12]} restored and healthy")
    return EXIT_RESTORED


def activate(host: Host, environment: str, sha: str, *, rollback: bool, prepared_runtime: bool) -> None:
    """Start a prepared release. prepared_runtime selects runtime.env.next written by the
    latest prepare; otherwise the release's established runtime.env is used."""
    layout = host.layout
    host.check_environment(environment)
    host.owner.check_path(layout.release_dir(sha), kind="dir")
    meta = read_json(layout.release_meta(sha))
    if tree_digest(layout.source_dir(sha)) != meta.get("tree_sha256"):
        raise DeployError(f"release {sha[:12]} does not match its recorded tree digest", EXIT_INVALID)
    config = host.load_config(sha)
    config.environment(environment)
    runtime = layout.runtime_next(sha) if prepared_runtime else layout.runtime(sha)
    if not runtime.exists():
        raise DeployError(f"release {sha[:12]} has no {runtime.name}; run prepare for it first", EXIT_INVALID)
    host.owner.check_path(runtime, kind="file")
    previous = current_sha(layout)
    backup_hook = layout.source_dir(sha) / "deploy" / "hooks" / "backup"
    if not rollback and environment == "production" and config.migrate and not os.path.lexists(backup_hook):
        raise DeployError(
            "production with migrations needs deploy/hooks/backup (pre-migration backup). Nothing was changed",
            EXIT_INVALID,
        )

    # Nothing that serves traffic changes until the release has passed these steps.
    host.run_hook("pre_build", config, environment, sha, runtime)
    ensure_images(host, config, environment, sha)
    if config.preflight:
        log("preflight: running in the new image")
        result = host.compose(
            config, environment, sha, runtime, "run", "--rm", "--no-deps", "-T", config.service, *config.preflight
        )
        if result.returncode != 0:
            raise DeployError(f"preflight failed (exit {result.returncode}). Nothing was changed")
    else:
        warn("preflight is disabled in deploy/release.toml")
    if config.candidate:
        candidate_check(host, config, environment, sha, runtime)
    else:
        warn("candidate check is disabled in deploy/release.toml")

    if rollback:
        log("rollback: skipping backup and migrations (schema changes need explicit handling)")
    else:
        backup = run_backup(host, config, environment, sha, runtime)
        if backup is not None:
            meta = read_json(layout.release_meta(sha))
            meta["last_backup"] = backup
            write_json(host.owner, layout.release_meta(sha), meta)
        host.run_hook("pre_migrate", config, environment, sha, runtime)
        if config.migrate:
            log("migrate: running before the new release starts")
            result = host.compose(
                config, environment, sha, runtime, "run", "--rm", "-T", config.migrate_service, *config.migrate
            )
            if result.returncode != 0:
                raise DeployError(f"migrations failed (exit {result.returncode}). The previous release keeps serving")

    try:
        start_release(host, config, environment, sha, runtime)
    except DeployError as error:
        warn(f"activation failed: {error}")
        code = restore_previous(host, environment, sha, previous)
        raise DeployError(f"activation of {sha[:12]} failed", code) from error
    log(f"release {sha[:12]} is running and healthy locally")


def command_activate(host: Host, args: argparse.Namespace) -> int:
    sha = require_sha(args.sha)
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    activate(host, environment, sha, rollback=False, prepared_runtime=True)
    return EXIT_OK


def promote(host: Host, environment: str, sha: str, *, prepared_runtime: bool) -> None:
    layout = host.layout
    config = host.load_config(sha)
    project = config.project(environment)
    revisions = host.running_revisions(config.app, project)
    if not revisions or any(revision != sha for revision in revisions):
        raise DeployError(
            f"refusing to promote {sha[:12]}: running revisions in {project} are {sorted(set(revisions)) or 'none'}"
        )
    link = layout.base / f".current-{secrets.token_hex(4)}"
    os.symlink(f"releases/{sha}", link)
    os.replace(link, layout.current)
    if prepared_runtime:
        if not layout.runtime_next(sha).exists():
            raise DeployError(f"release {sha[:12]} has no prepared runtime to promote", EXIT_INVALID)
        os.replace(layout.runtime_next(sha), layout.runtime(sha))
    meta = read_json(layout.release_meta(sha))
    meta["promoted_at"] = utc_now_text(host.deps.now())
    write_json(host.owner, layout.release_meta(sha), meta)
    log(f"current -> {sha[:12]}")
    host.run_hook("post_promote", config, environment, sha, layout.runtime(sha))


def command_promote(host: Host, args: argparse.Namespace) -> int:
    sha = require_sha(args.sha)
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    promote(host, environment, sha, prepared_runtime=True)
    return EXIT_OK


def previous_release(layout: Layout, exclude: str | None) -> str | None:
    """The most recently promoted retained release other than `exclude`."""
    best: tuple[str, str] | None = None
    for sha in release_shas(layout):
        if sha == exclude:
            continue
        promoted = read_json(layout.release_meta(sha)).get("promoted_at")
        if promoted and (best is None or promoted > best[0]):
            best = (promoted, sha)
    return best[1] if best else None


def command_rollback(host: Host, args: argparse.Namespace) -> int:
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    current = current_sha(host.layout)
    target = require_sha(args.sha) if args.sha else previous_release(host.layout, current)
    if target is None:
        raise DeployError("no previous release to roll back to", EXIT_INVALID)
    if not host.layout.release_dir(target).is_dir():
        raise DeployError(
            f"release {target[:12]} is not retained on this host. Redeploy it through CI rollback", EXIT_INVALID
        )
    log(f"rolling back {environment} from {current[:12] if current else 'nothing'} to {target[:12]}")
    activate(host, environment, target, rollback=True, prepared_runtime=args.use_prepared_runtime)
    promote(host, environment, target, prepared_runtime=args.use_prepared_runtime)
    return EXIT_OK


def command_cleanup(host: Host, args: argparse.Namespace) -> int:
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    layout = host.layout
    current = current_sha(layout)
    if current is None:
        log("cleanup: nothing is current. Skipping")
        return EXIT_OK
    config = host.load_config(current)
    keep = args.keep if args.keep is not None else config.environment(environment).keep
    running = set(host.running_revisions(config.app))
    ordered = sorted(
        release_shas(layout),
        key=lambda sha: read_json(layout.release_meta(sha)).get("promoted_at") or "",
        reverse=True,
    )
    promoted = [sha for sha in ordered if read_json(layout.release_meta(sha)).get("promoted_at")]
    retained = set(promoted[:keep]) | {current} | running
    removed = 0
    for sha in ordered:
        if sha in retained:
            continue
        meta = read_json(layout.release_meta(sha))
        failed = False
        for tag in sorted(meta.get("image_tags", {}).values()):
            if not tag.endswith(f":{sha}"):
                raise DeployError(f"release {sha[:12]} records an unexpected image tag {tag}", EXIT_INVALID)
            if host.inspect_image(tag) is None:
                continue
            result = host.docker("image", "rm", tag)
            if result.returncode != 0:
                warn(f"could not remove image {tag}; keeping release {sha[:12]}")
                failed = True
        if failed:
            continue
        target = layout.release_dir(sha)
        if target.parent != layout.releases or not SHA_PATTERN.match(target.name):
            raise DeployError(f"refusing to remove unexpected path {target}", EXIT_INVALID)
        remove_tree(target)
        removed += 1
        log(f"cleanup: removed release {sha[:12]} and its images")
    log(f"cleanup: kept {len([sha for sha in ordered if sha in retained])} release(s), removed {removed} (keep {keep})")
    return EXIT_OK


def command_record(host: Host, args: argparse.Namespace) -> int:
    sha = require_sha(args.sha)
    environment = require_environment(args.environment)
    host.require_lock(args.lock_token)
    if args.result not in RECORD_RESULTS or args.mode not in RECORD_MODES:
        raise DeployError("invalid --result or --mode", EXIT_INVALID)
    if args.actor and not ACTOR_PATTERN.match(args.actor):
        raise DeployError("--actor must be a plain user name", EXIT_INVALID)
    if args.pipeline_url and not PIPELINE_URL_PATTERN.match(args.pipeline_url):
        raise DeployError("--pipeline-url must be a plain https URL", EXIT_INVALID)
    if args.previous:
        require_sha(args.previous)
    meta = read_json(host.layout.release_meta(sha))
    record = {
        "environment": environment,
        "sha": sha,
        "mode": args.mode,
        "result": args.result,
        "time": utc_now_text(host.deps.now()),
        "actor": args.actor or "unknown",
        "pipeline_url": args.pipeline_url or "",
        "previous_sha": args.previous or "",
        "current_sha": current_sha(host.layout) or "",
        "archive_sha256": meta.get("archive_sha256", ""),
        "tree_sha256": meta.get("tree_sha256", ""),
        "images": meta.get("images", {}),
        "backup": meta.get("last_backup", "") if args.mode == "deploy" else "",
    }
    write_json(host.owner, host.layout.record, record)
    line = json.dumps(record, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(host.layout.history, flags, 0o600), "a", encoding="utf-8") as handle:
        handle.write(line)
    host.owner.check_path(host.layout.history, kind="file")
    log(f"recorded {args.mode} of {sha[:12]} to {environment}: {args.result}")
    return EXIT_OK


def command_status(host: Host, args: argparse.Namespace) -> int:
    layout = host.layout
    current = current_sha(layout) if layout.base.is_dir() else None
    if args.field == "current":
        print(current or "")
        return EXIT_OK
    if args.field == "previous":
        print(previous_release(layout, current) or "")
        return EXIT_OK
    lock_held = False
    if layout.lock_file.exists():
        descriptor = os.open(layout.lock_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            lock_held = True
        finally:
            os.close(descriptor)
    releases = []
    for sha in release_shas(layout):
        meta = read_json(layout.release_meta(sha))
        releases.append(
            {
                "sha": sha,
                "prepared_at": meta.get("prepared_at", ""),
                "promoted_at": meta.get("promoted_at", ""),
                "images": meta.get("images", {}),
            }
        )
    running: list[str] | str = "unknown"
    if current is not None:
        try:
            config = host.load_config(current)
            environment = layout.environment_marker.read_text(encoding="utf-8").strip()
            running = sorted(set(host.running_revisions(config.app, config.project(environment))))
        except (DeployError, OSError) as error:
            running = f"unknown ({error})"
    status = {
        "base": str(layout.base),
        "current": current or "",
        "lock_held": lock_held,
        "releases": releases,
        "running_revisions": running,
        "last_record": read_json(layout.record),
    }
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"base: {status['base']}")
    print(f"current: {status['current'] or '(none)'}")
    print(f"lock held: {'yes' if lock_held else 'no'}")
    print(f"running revisions: {running}")
    for release in releases:
        marker = "*" if release["sha"] == current else " "
        print(
            f" {marker} {release['sha']} prepared {release['prepared_at'] or '-'}"
            f" promoted {release['promoted_at'] or '-'}"
        )
    record = status["last_record"]
    if record:
        print(
            f"last record: {record.get('mode')} {record.get('sha', '')[:12]} {record.get('result')}"
            f" at {record.get('time')} by {record.get('actor')}"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_host.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(dest="command_name", required=True)

    def add(
        name: str, help_text: str, *, environment: bool = True, sha: str = "", lock: bool = True
    ) -> argparse.ArgumentParser:
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("--base", required=True, help="environment directory, for example <DEPLOY_ROOT>/<env>")
        sub.add_argument("--owner", required=True, choices=("root", "self"))
        if environment:
            sub.add_argument("--environment", required=True)
        if sha == "required":
            sub.add_argument("--sha", required=True)
        elif sha == "optional":
            sub.add_argument("--sha")
        if lock:
            sub.add_argument("--lock-token", default=os.environ.get("AF_LOCK_TOKEN"))
        return sub

    lock = add("lock", "hold the environment lock or run a command under it", environment=False, lock=False)
    lock.add_argument("--wait", type=int, default=600, help="seconds to wait for the lock (default 600)")
    lock.add_argument("--hold", action="store_true", help="hold the lock until standard input closes")
    lock.add_argument("--token", help="token that later commands pass as --lock-token")
    lock.add_argument("command", nargs=argparse.REMAINDER)

    secure = add("secure-file", "take custody of an uploaded file", environment=False)
    secure.add_argument("path")

    prepare = add("prepare", "extract and verify a release", sha="required")
    prepare.add_argument("--archive", required=True)
    prepare.add_argument("--runtime", required=True)
    prepare.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    add("activate", "start a prepared release with automatic restore", sha="required")
    add("promote", "point current at the running release", sha="required")
    rollback = add("rollback", "activate and promote a retained release", sha="optional")
    rollback.add_argument(
        "--use-prepared-runtime",
        action="store_true",
        help="use the runtime from the latest prepare (CI rollback) instead of the release's established one",
    )
    cleanup = add("cleanup", "remove releases beyond retention")
    cleanup.add_argument("--keep", type=int)

    record = add("record", "write the deployment record", sha="required")
    record.add_argument("--result", required=True, choices=RECORD_RESULTS)
    record.add_argument("--mode", default="deploy", choices=RECORD_MODES)
    record.add_argument("--actor", default="")
    record.add_argument("--pipeline-url", default="")
    record.add_argument("--previous", default="")

    status = add("status", "show releases, lock, and the last record", environment=False, lock=False)
    status.add_argument("--json", action="store_true")
    status.add_argument("--field", choices=("current", "previous"))
    return parser


COMMANDS = {
    "lock": command_lock,
    "secure-file": command_secure_file,
    "prepare": command_prepare,
    "activate": command_activate,
    "promote": command_promote,
    "rollback": command_rollback,
    "cleanup": command_cleanup,
    "record": command_record,
    "status": command_status,
}


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.umask(0o077)
    try:
        host = Host(args.base, args.owner, deps or Deps())
        return COMMANDS[args.command_name](host, args)
    except DeployError as error:
        print(f"release_host: ERROR: {error}", file=sys.stderr, flush=True)
        return error.exit_code
    except OSError as error:
        print(f"release_host: ERROR: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
