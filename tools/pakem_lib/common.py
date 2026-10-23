"""Shared helpers: errors, hashing, home-relative paths, and reporting."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

IGNORED_FILE_NAMES = frozenset({".DS_Store"})
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


# Exit codes shared by every workflow command (docs/pakem-command-map.md, Conventions).
EXIT_DONE = 0
EXIT_USAGE = 2
EXIT_OWNER_ACTION = 3
EXIT_GAPS = 4
EXIT_CHECK_FAILED = 5
EXIT_REFUSED = 6
EXIT_BUSY = 75


class PakemError(Exception):
    """A user-facing error that aborts the current command."""

    exit_code = EXIT_USAGE


class CommandRefused(PakemError):
    """A safety rule stopped the command; nothing was changed."""

    exit_code = EXIT_REFUSED


class OwnerActionRequired(PakemError):
    """Only the owner can take the next step; the message says exactly which."""

    exit_code = EXIT_OWNER_ACTION


class GapsOpen(PakemError):
    """Required project decisions are still open; the message names them."""

    exit_code = EXIT_GAPS


class CheckFailed(PakemError):
    """The command ran, and the check that proves its result did not pass."""

    exit_code = EXIT_CHECK_FAILED


class CommandBusy(PakemError):
    """Another run holds the lock this command needs."""

    exit_code = EXIT_BUSY


@dataclass
class Report:
    """Collects errors and warnings so a command can report them together."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def extend(self, other: "Report", prefix: str = "") -> None:
        self.errors.extend(prefix + message for message in other.errors)
        self.warnings.extend(prefix + message for message in other.warnings)

    def print(self) -> None:
        for message in self.errors:
            print(f"ERROR: {message}")
        for message in self.warnings:
            print(f"WARNING: {message}")
        print(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 1
        return 0


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text: str) -> str:
    """Hex sha256 of text after LF normalization, encoded as UTF-8."""
    return hashlib.sha256(normalize_newlines(text).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PakemError(f"missing file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise PakemError(f"file is not valid UTF-8: {path}") from exc


def home_dir() -> Path:
    # Path.home() honours $HOME, which is what lets tests run against a temporary home.
    return Path.home()


def expand_user_path(raw: str) -> Path:
    if raw == "~":
        return home_dir()
    if raw.startswith("~/"):
        return home_dir() / raw[2:]
    path = Path(raw)
    if not path.is_absolute():
        raise PakemError(f"path must be absolute or start with ~/: {raw}")
    return path


def display_path(path: Path) -> str:
    """Render a path as ~/... when it lives under HOME, otherwise absolute."""
    home = home_dir()
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    return "~/" + relative.as_posix()


def tree_files(directory: Path) -> list[str]:
    """Sorted POSIX relative paths of regular files under a directory."""
    relative_paths: list[str] = []
    for current_root, dir_names, file_names in os.walk(directory):
        dir_names.sort()
        for file_name in file_names:
            if file_name in IGNORED_FILE_NAMES:
                continue
            absolute = Path(current_root) / file_name
            relative_paths.append(absolute.relative_to(directory).as_posix())
    relative_paths.sort()
    return relative_paths


def tree_sha(directory: Path) -> str:
    """Hash of sorted relative paths plus contents; stable across copies."""
    hasher = hashlib.sha256()
    for relative in tree_files(directory):
        content = (directory / relative).read_bytes()
        hasher.update(f"{relative}\0{len(content)}\0".encode("utf-8"))
        hasher.update(content)
    return hasher.hexdigest()


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise PakemError(f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()}")
    return completed


def is_git_work_tree(path: Path) -> bool:
    completed = run_git(path, "rev-parse", "--is-inside-work-tree", check=False)
    return completed.returncode == 0 and completed.stdout.strip() == "true"


TOML_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def toml_string(text: str) -> str:
    """A TOML basic string. JSON is not one: it writes characters outside the BMP as
    surrogate pairs, and leaves DEL unescaped, both of which TOML rejects."""
    parts = []
    for character in text:
        code = ord(character)
        if character in TOML_ESCAPES:
            parts.append(TOML_ESCAPES[character])
        elif 0xD800 <= code <= 0xDFFF:
            raise PakemError(f"text holds an unpaired surrogate (U+{code:04X}), which TOML cannot store")
        elif code < 0x20 or code == 0x7F:
            parts.append(f"\\u{code:04X}")
        else:
            parts.append(character)
    return '"' + "".join(parts) + '"'


def toml_value(value: object) -> str:
    """A TOML value for the plain types Pakem writes: strings, booleans, integers, lists."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise PakemError(f"cannot write {type(value).__name__} as a TOML value")


def write_text_atomically(path: Path, text: str, mode: int | None = None) -> None:
    """Write via a sibling temp file and os.replace so a symlink at path is replaced, not followed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.pakem-tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def confirm_steps(assume_yes: bool, command: str) -> None:
    """The one confirmation a workflow command asks before acting (`--yes` skips it)."""
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise PakemError(f"pass --yes to run {command} without a prompt")
    if input("Apply these steps? [y/N] ").strip().lower() not in ("y", "yes"):
        raise CommandRefused(f"{command} cancelled; nothing changed")


@dataclass(frozen=True)
class Framework:
    """Locations inside a framework checkout."""

    root: Path

    @property
    def version_path(self) -> Path:
        return self.root / "VERSION"

    @property
    def kernel_path(self) -> Path:
        return self.root / "kernel" / "KERNEL.md"

    @property
    def targets_path(self) -> Path:
        return self.root / "hosts" / "targets.toml"

    @property
    def skills_root(self) -> Path:
        return self.root / "skills"

    @property
    def project_template_path(self) -> Path:
        return self.root / "skills" / "project-bootstrap" / "assets" / "project" / "AGENTS.md.tmpl"

    @property
    def capabilities_path(self) -> Path:
        return self.root / "standards" / "capabilities.toml"

    @property
    def banned_patterns_path(self) -> Path:
        return self.root / "lint" / "banned.toml"

    @property
    def hooks_dir(self) -> Path:
        return self.root / "hooks"

    @property
    def enforcement_dir(self) -> Path:
        return self.root / "enforcement"

    @property
    def changelog_path(self) -> Path:
        return self.root / "CHANGELOG.md"

    @property
    def changes_dir(self) -> Path:
        return self.root / "changes"

    def version(self) -> str:
        version = read_text(self.version_path).strip()
        if not SEMVER_PATTERN.match(version):
            raise PakemError(f"VERSION is not semver: {version!r}")
        return version


def version_tuple(version: str) -> tuple[int, ...]:
    """Numeric release part of a semver string, e.g. '0.1.0-rc1' -> (0, 1, 0)."""
    release = re.split(r"[-+]", version, maxsplit=1)[0]
    parts: list[int] = []
    for piece in release.split("."):
        if not piece.isdigit():
            raise PakemError(f"invalid version: {version!r}")
        parts.append(int(piece))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)
