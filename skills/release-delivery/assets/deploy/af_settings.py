"""Build an environment's runtime settings from its four sources (contracts v1, section 4).

- defaults: `deploy/env/<environment>.env` from the release bundle (committed, not secret);
- placement: the host's private `placement.env` (pull) or CI-supplied values (push);
- secrets: private files `<secrets>/<NAME>`, set once by the operator;
- host_generated: created here once, into the same secrets directory.

The runtime file holds defaults and placement as `NAME='value'` lines (Compose reads them
literally) plus `NAME_FILE=/run/secrets/<name>` for every secret; secret values never enter
it. Errors and results name settings, never values. Standard library only.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

import contracts

NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
MAX_VALUE_LENGTH = 4096
MAX_SECRET_BYTES = 64 * 1024
# Owner read-write, group read: containers join the deploying user's group to read secrets.
SECRET_MODE = 0o640
CONTAINER_SECRETS = "/run/secrets"


class SettingsError(Exception):
    """The settings cannot produce a valid runtime; nothing was written."""


@dataclass(frozen=True)
class Runtime:
    path: Path
    names: list[str]
    secret_names: list[str]
    generated: list[str]


def parse_env_text(text: str, label: str) -> dict[str, str]:
    """NAME=value lines read literally; quotes, export, and inline comments are refused
    because dotenv readers disagree about them."""
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not NAME.fullmatch(name):
            raise SettingsError(f"{label}:{number}: expected NAME=value")
        if value[:1] in ("'", '"') or " #" in value:
            raise SettingsError(f"{label}:{number}: {name}: write the value without quotes or inline comments")
        if name in values:
            raise SettingsError(f"{label}:{number}: {name} is defined twice")
        values[name] = value
    return values


def value_problem(value: str) -> str | None:
    if any(character in value for character in "\n\r\x00"):
        return "must be a single line"
    if "'" in value:
        return "must not contain a single quote"
    if value != value.strip():
        return "must not start or end with whitespace"
    if len(value) > MAX_VALUE_LENGTH:
        return f"must be at most {MAX_VALUE_LENGTH} characters"
    return None


def _private_file(path: Path, label: str) -> None:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise SettingsError(f"{label} must be a regular file")
    if info.st_mode & 0o077:
        raise SettingsError(f"{label} must not be readable by others (mode 0600)")
    if info.st_uid != os.getuid():
        raise SettingsError(f"{label} must be owned by the deploying user")


def _secret_file(path: Path, name: str) -> None:
    """A secret file is owned by the deploying user, readable by its own group only (0640).

    Compose mounts file secrets with the host owner and mode, so containers running as any
    user read them through `group_add: ["${AF_SECRETS_GID}"]`. A 0600 file from an older
    engine is opened up to 0640 here.
    """
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise SettingsError(f"secret {name} must be a regular file")
    if info.st_uid != os.getuid() or info.st_gid != os.getgid():
        raise SettingsError(f"secret {name} must be owned by the deploying user and its group")
    if info.st_mode & 0o037:
        raise SettingsError(f"secret {name} must not be writable by the group or readable by others (mode 0640)")
    if stat.S_IMODE(info.st_mode) != SECRET_MODE:
        os.chmod(path, SECRET_MODE)


def _private_directory(path: Path, label: str) -> None:
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise SettingsError(f"{label} must be a private directory (0700) owned by the deploying user")


def secret_file_name(name: str) -> str:
    return name.lower()


def generate(spec: str) -> str:
    kind, _, size = spec.partition(":")
    if kind == "token_urlsafe":
        return secrets.token_urlsafe(int(size))
    if kind == "token_hex":
        return secrets.token_hex(int(size))
    raise SettingsError("unknown generator")


def _write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def set_secret(secrets_dir: Path, name: str, value: bytes) -> None:
    """Store one operator-provided secret (read from stdin by the CLI, never argv)."""
    if not NAME.fullmatch(name):
        raise SettingsError("secret names are UPPER_CASE")
    if not value or len(value) > MAX_SECRET_BYTES:
        raise SettingsError(f"{name}: secret must be 1 to {MAX_SECRET_BYTES} bytes")
    _private_directory(secrets_dir, "secrets directory")
    _write_private(secrets_dir / name, value, SECRET_MODE)


def ensure_secrets_directory(secrets_dir: Path) -> None:
    """The secrets directory the deploy user owns (0700); made when a host predates it."""
    if not os.path.lexists(secrets_dir):
        secrets_dir.mkdir(mode=0o700)
    _private_directory(secrets_dir, "secrets directory")


def secret_age(secrets_dir: Path, name: str, now: float) -> int | None:
    """Seconds since a secret was last written, or None when it is not set."""
    path = secrets_dir / name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        return None
    return max(0, int(now - info.st_mtime))


def generate_token(size: int) -> str:
    return secrets.token_urlsafe(size)


def read_placement(path: Path) -> dict[str, str]:
    """The host's placement values, as the engine reads them; empty when there is no file."""
    if not os.path.lexists(path):
        return {}
    _private_file(path, "placement.env")
    return parse_env_text(path.read_text(encoding="utf-8"), "placement.env")


def set_placement(path: Path, values: dict[str, str]) -> None:
    """Merge NAME=value pairs into placement.env (0600, written atomically). Other lines,
    comments included, stay as they are; the result must read as the engine reads it."""
    for name, value in values.items():
        if not NAME.fullmatch(name):
            raise SettingsError("placement names are UPPER_CASE")
        issue = "must not be empty" if not value else value_problem(value)
        if issue:
            raise SettingsError(f"{name}: {issue}")
    remaining = dict(values)
    lines: list[str] = []
    if os.path.lexists(path):
        _private_file(path, "placement.env")
        current = path.read_text(encoding="utf-8")
        parse_env_text(current, "placement.env")
        for line in current.splitlines():
            name, separator, _ = line.partition("=")
            if separator and name in remaining:
                lines.append(f"{name}={remaining.pop(name)}")
            else:
                lines.append(line)
    lines += [f"{name}={value}" for name, value in remaining.items()]
    text = "\n".join(lines) + "\n"
    parse_env_text(text, "placement.env")
    _write_private(path, text.encode("utf-8"), 0o600)


def build_runtime(
    bundle: Path,
    environment: str,
    secrets_dir: Path,
    output: Path,
    *,
    placement_file: Path | None = None,
    placement_values: dict[str, str] | None = None,
) -> Runtime:
    """Check every source against the manifest and write the runtime file (0600)."""
    manifest = tomllib.loads((bundle / "deploy/settings.toml").read_text(encoding="utf-8"))
    problems = contracts.validate_settings_manifest(manifest)
    if problems:
        raise SettingsError("; ".join(problems))
    placement_names = set(manifest.get("placement", []))
    secret_names = list(manifest.get("secrets", []))
    generated_names = list(manifest.get("host_generated", []))
    reserved = placement_names | set(secret_names) | set(generated_names)

    defaults_path = manifest.get("defaults", {}).get(environment)
    defaults: dict[str, str] = {}
    if defaults_path is not None:
        relative = Path(defaults_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SettingsError("defaults path must stay inside the bundle")
        text = (bundle / relative).read_text(encoding="utf-8")
        problems = contracts.validate_defaults_file(text, manifest)
        if problems:
            raise SettingsError("; ".join(problems))
        defaults = parse_env_text(text, relative.name)

    if placement_file is not None and placement_values is not None:
        raise SettingsError("placement comes from the host file or from CI, not both")
    placement: dict[str, str] = {}
    if placement_file is not None:
        if placement_file.exists():
            _private_file(placement_file, "placement.env")
            placement = parse_env_text(placement_file.read_text(encoding="utf-8"), "placement.env")
    elif placement_values is not None:
        placement = {name: value for name, value in placement_values.items() if name in placement_names}
    unknown = sorted(set(placement) - placement_names)
    if unknown:
        raise SettingsError(f"placement.env sets names the manifest does not place: {', '.join(unknown)}")

    _private_directory(secrets_dir, "secrets directory")
    generated: list[str] = []
    for name in generated_names:
        path = secrets_dir / name
        if not path.exists():
            _write_private(path, generate(manifest["generators"][name]).encode(), SECRET_MODE)
            generated.append(name)

    values = {**defaults, **placement}
    present_secrets = []
    for name in [*secret_names, *generated_names]:
        path = secrets_dir / name
        if path.exists():
            _secret_file(path, name)
            if path.stat().st_size == 0 or path.stat().st_size > MAX_SECRET_BYTES:
                raise SettingsError(f"secret {name} must be 1 to {MAX_SECRET_BYTES} bytes")
            present_secrets.append(name)

    problems = []
    for name in manifest.get("required", []):
        if name in secret_names or name in generated_names:
            if name not in present_secrets:
                problems.append(f"{name}: required secret file is missing")
        elif not values.get(name):
            problems.append(f"{name}: required value is missing")
    for name, value in values.items():
        if name in reserved and name not in placement_names:
            problems.append(f"{name}: has another source than defaults or placement")
        issue = value_problem(value)
        if issue:
            problems.append(f"{name}: {issue}")
    for pair in manifest.get("pairs", []):
        set_count = sum(bool(name in present_secrets or values.get(name)) for name in pair)
        if 0 < set_count < len(pair):
            problems.append(f"{', '.join(pair)}: set all of them or none")
    for name, pattern in manifest.get("formats", {}).items():
        if values.get(name) and re.fullmatch(pattern, values[name]) is None:
            problems.append(f"{name}: does not match its format")
    if environment in manifest.get("production_environments", ["production"]):
        for name, expected in manifest.get("prod_assert", {}).items():
            if values.get(name) != expected:
                problems.append(f"{name}: production requires a specific value")
    if problems:
        raise SettingsError("; ".join(problems))

    lines = [f"# Runtime for {environment}, written by the release engine. Do not commit or print."]
    lines.append(f"AF_SECRETS_DIR='{secrets_dir}'")
    lines += [f"{name}='{values[name]}'" for name in sorted(values)]
    lines += [f"{name}_FILE='{CONTAINER_SECRETS}/{secret_file_name(name)}'" for name in sorted(present_secrets)]
    _write_private(output, ("\n".join(lines) + "\n").encode("utf-8"))
    return Runtime(output, sorted(values), sorted(present_secrets), generated)
