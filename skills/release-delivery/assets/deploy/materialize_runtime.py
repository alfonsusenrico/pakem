#!/usr/bin/env python3
"""Build one environment's runtime file from a committed manifest and CI variables.

Usage:
  materialize_runtime.py --environment ENV --output FILE
                         [--root DIR] [--manifest deploy/runtime.manifest.toml]
                         [--values-json-env NAME ...]

The manifest (see runtime.manifest.example.toml) names a committed defaults file per
environment and the variables CI must (`required`) or may (`optional`) supply, pairs
that must be set together, variables generated on the host (`host_generated`, never
supplied by CI), single-line format rules, and assertions for production.

Values come only from variables the manifest names: the process environment, then each
--values-json-env variable in order (a JSON object, for example GitHub's toJSON(secrets)),
later sources winning. The output is written owner-only (0600) as NAME='value' lines,
the literal form Docker Compose reads without interpolation. Values are never printed:
errors and the summary name variables only.

Exit codes: 0 written, 1 validation failed, 2 invalid usage or manifest.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.stderr.write("materialize_runtime: ERROR: Python 3.11 or later is required\n")
    sys.exit(2)

import argparse
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
ENVIRONMENT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAX_VALUE_LENGTH = 8192
MANIFEST_KEYS = {
    "defaults",
    "required",
    "optional",
    "pairs",
    "host_generated",
    "formats",
    "prod_assert",
    "production_environments",
}
HOST_GENERATED_HEADER = "# af:host-generated"


class ManifestError(Exception):
    """The manifest, a defaults file, or the command line is invalid (exit 2)."""


@dataclass
class Manifest:
    defaults: dict[str, str]
    required: list[str]
    optional: list[str]
    pairs: list[list[str]]
    host_generated: list[str]
    formats: dict[str, re.Pattern[str]]
    prod_assert: dict[str, str]
    production_environments: list[str]


@dataclass
class Result:
    entries: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    from_ci: int = 0
    from_defaults: int = 0


def _names(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(name, str) and NAME_PATTERN.match(name) for name in value):
        raise ManifestError(f"manifest: {key} must be a list of variable names (A-Z, 0-9, _)")
    if len(set(value)) != len(value):
        raise ManifestError(f"manifest: {key} lists a name twice")
    return list(value)


def _relative_file(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
        raise ManifestError(f"manifest: {label} must be a relative path inside the repository")
    return root / value


def load_manifest(path: Path, root: Path) -> Manifest:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"manifest not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"manifest is not valid TOML: {error}") from error
    unknown = set(data) - MANIFEST_KEYS
    if unknown:
        raise ManifestError(f"manifest: unknown keys {sorted(unknown)}")

    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or not defaults:
        raise ManifestError("manifest: [defaults] must map each environment to its defaults file")
    for environment, file_name in defaults.items():
        if not ENVIRONMENT_PATTERN.match(environment):
            raise ManifestError(f"manifest: invalid environment name in [defaults]: {environment}")
        _relative_file(root, file_name, f"defaults.{environment}")

    required = _names(data, "required")
    optional = _names(data, "optional")
    host_generated = _names(data, "host_generated")
    overlap = (
        (set(required) & set(optional)) | (set(required) & set(host_generated)) | (set(optional) & set(host_generated))
    )
    if overlap:
        raise ManifestError(
            f"manifest: a name may appear in only one of required, optional, host_generated: {sorted(overlap)}"
        )

    pairs = data.get("pairs", [])
    if not isinstance(pairs, list):
        raise ManifestError("manifest: pairs must be a list of name lists")
    for group in pairs:
        if (
            not isinstance(group, list)
            or len(group) < 2
            or not all(isinstance(name, str) and name in optional for name in group)
        ):
            raise ManifestError("manifest: each pair must list two or more names from optional")

    raw_formats = data.get("formats", {})
    if not isinstance(raw_formats, dict):
        raise ManifestError("manifest: [formats] must map names to regular expressions")
    formats: dict[str, re.Pattern[str]] = {}
    for name, pattern in raw_formats.items():
        if not NAME_PATTERN.match(name) or not isinstance(pattern, str):
            raise ManifestError(f"manifest: formats.{name} must be a regular expression string")
        try:
            formats[name] = re.compile(pattern)
        except re.error as error:
            raise ManifestError(f"manifest: formats.{name} is not a valid regular expression: {error}") from error

    prod_assert = data.get("prod_assert", {})
    if not isinstance(prod_assert, dict) or not all(
        NAME_PATTERN.match(name) and isinstance(value, str) for name, value in prod_assert.items()
    ):
        raise ManifestError("manifest: [prod_assert] must map names to exact string values")
    if set(prod_assert) & (set(required) | set(host_generated)):
        raise ManifestError("manifest: prod_assert may only name committed defaults or optional variables")

    production_environments = data.get("production_environments", ["production"])
    if not isinstance(production_environments, list) or not all(
        isinstance(name, str) for name in production_environments
    ):
        raise ManifestError("manifest: production_environments must be a list of environment names")

    return Manifest(
        defaults=dict(defaults),
        required=required,
        optional=optional,
        pairs=[list(group) for group in pairs],
        host_generated=host_generated,
        formats=formats,
        prod_assert=dict(prod_assert),
        production_environments=list(production_environments),
    )


def read_defaults(path: Path) -> dict[str, str]:
    """Parse NAME=value lines literally; quotes, export, and inline comments are refused
    because dotenv readers disagree about them."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ManifestError(f"defaults file not found: {path}") from error
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not NAME_PATTERN.match(name):
            raise ManifestError(f"{path.name}:{number}: expected NAME=value")
        if value[:1] in ("'", '"') or " #" in value:
            raise ManifestError(f"{path.name}:{number}: {name}: write the value without quotes or inline comments")
        if name in values:
            raise ManifestError(f"{path.name}:{number}: {name} is defined twice")
        values[name] = value
    return values


def value_problem(value: str) -> str | None:
    """Why a value cannot be written as a single-line, single-quoted runtime value."""
    if any(character in value for character in "\n\r\x00"):
        return "must be a single line"
    if "'" in value:
        return "must not contain a single quote"
    if value != value.strip():
        return "must not start or end with whitespace"
    if len(value) > MAX_VALUE_LENGTH:
        return f"must be at most {MAX_VALUE_LENGTH} characters"
    return None


def collect_sources(json_env_names: list[str], environ: dict[str, str]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = [dict(environ)]
    for env_name in json_env_names:
        if not NAME_PATTERN.match(env_name):
            raise ManifestError(f"--values-json-env takes a variable name, got {env_name!r}")
        raw = environ.get(env_name)
        if raw is None or raw == "":
            raise ManifestError(f"--values-json-env {env_name}: the variable is not set")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ManifestError(f"--values-json-env {env_name}: not a JSON object") from error
        if not isinstance(data, dict):
            raise ManifestError(f"--values-json-env {env_name}: not a JSON object")
        sources.append({key: value for key, value in data.items() if isinstance(value, str)})
    return sources


def lookup(name: str, sources: list[dict[str, str]]) -> str | None:
    found = None
    for source in sources:
        if name in source:
            found = source[name]
    return found


def materialize(manifest: Manifest, root: Path, environment: str, sources: list[dict[str, str]]) -> Result:
    if environment not in manifest.defaults:
        raise ManifestError(f"manifest has no defaults file for environment {environment!r}")
    defaults = read_defaults(_relative_file(root, manifest.defaults[environment], f"defaults.{environment}"))
    result = Result()

    for name in defaults:
        if name in manifest.required or name in manifest.host_generated:
            result.errors.append(
                f"{name} must not appear in the committed defaults (it is required from CI or host-generated)"
            )
    result.entries.update(defaults)
    result.from_defaults = len(defaults)

    for name in manifest.host_generated:
        if lookup(name, sources) is not None:
            result.errors.append(f"{name} is host-generated and must not be supplied by CI")

    missing = []
    for name in manifest.required:
        value = lookup(name, sources)
        if value is None or value == "":
            missing.append(name)
            continue
        result.entries[name] = value
        result.from_ci += 1
    if missing:
        result.errors.append(f"missing required variables: {', '.join(missing)}")

    for name in manifest.optional:
        value = lookup(name, sources)
        if value is not None and value != "":
            if name in defaults:
                result.from_defaults -= 1
            result.entries[name] = value
            result.from_ci += 1

    for group in manifest.pairs:
        present = [name for name in group if result.entries.get(name, "") != ""]
        if present and len(present) != len(group):
            absent = [name for name in group if name not in present]
            result.errors.append(f"{', '.join(group)} must be set together (missing {', '.join(absent)})")

    for name, value in result.entries.items():
        problem = value_problem(value)
        if problem is not None:
            result.errors.append(f"{name} {problem} (value not shown)")

    for name, pattern in manifest.formats.items():
        if name in result.entries and result.entries[name] != "" and pattern.fullmatch(result.entries[name]) is None:
            result.errors.append(f"{name} does not match its format in the manifest (value not shown)")

    if environment in manifest.production_environments:
        for name, expected in manifest.prod_assert.items():
            if result.entries.get(name) != expected:
                result.errors.append(f"production assertion failed: {name} must be {expected!r}")
    return result


def write_runtime(path: Path, entries: dict[str, str], host_generated: list[str], environment: str) -> None:
    lines = [f"# Runtime for {environment}, written by materialize_runtime.py. Do not commit or print."]
    if host_generated:
        lines.append(f"{HOST_GENERATED_HEADER} {' '.join(host_generated)}")
    lines += [f"{name}='{entries[name]}'" for name in sorted(entries)]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="materialize_runtime.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=".", help="repository root the manifest paths are relative to")
    parser.add_argument("--manifest", default="deploy/runtime.manifest.toml")
    parser.add_argument("--values-json-env", action="append", default=[], metavar="NAME")
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    try:
        if not ENVIRONMENT_PATTERN.match(args.environment):
            raise ManifestError("--environment must match [a-z][a-z0-9-]{0,31}")
        root = Path(args.root)
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        manifest = load_manifest(manifest_path, root)
        sources = collect_sources(args.values_json_env, environ)
        result = materialize(manifest, root, args.environment, sources)
    except ManifestError as error:
        print(f"materialize_runtime: ERROR: {error}", file=sys.stderr)
        return 2
    if result.errors:
        for message in result.errors:
            print(f"materialize_runtime: ERROR: {message}", file=sys.stderr)
        print(f"materialize_runtime: {len(result.errors)} problem(s). No runtime file written", file=sys.stderr)
        return 1
    write_runtime(Path(args.output), result.entries, manifest.host_generated, args.environment)
    print(
        f"materialize_runtime: {args.environment}: {len(result.entries)} variable(s) "
        f"({result.from_ci} from CI, {result.from_defaults} committed defaults), "
        f"{len(manifest.host_generated)} host-generated on the host"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
