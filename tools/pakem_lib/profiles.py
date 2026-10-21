"""Profiles: optional add-on digests and skills that live outside the framework repo."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import PakemError, read_text, version_tuple

PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
CONSTRAINT_PATTERN = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})$")


@dataclass(frozen=True)
class Profile:
    name: str
    version: str
    root: Path
    digest_path: Path
    skills_dir: Path | None

    def digest_text(self) -> str:
        return read_text(self.digest_path)


def version_satisfies(version: str, specification: str) -> bool:
    """Check a version against comma-separated constraints such as '>=0.1,<2.0'."""
    actual = version_tuple(version)
    for raw_constraint in specification.split(","):
        constraint = raw_constraint.strip()
        if not constraint:
            continue
        match = CONSTRAINT_PATTERN.match(constraint)
        if match is None:
            raise PakemError(f"unsupported version constraint: {constraint!r}")
        operator, bound_text = match.groups()
        bound = version_tuple(bound_text)
        satisfied = {
            ">=": actual >= bound,
            "<=": actual <= bound,
            "==": actual == bound,
            "!=": actual != bound,
            ">": actual > bound,
            "<": actual < bound,
        }[operator]
        if not satisfied:
            return False
    return True


def _resolve_inside(root: Path, relative: str, key: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise PakemError(f"profile {key} escapes the profile directory: {relative}")
    return candidate


def load_profile(directory: Path, framework_version: str) -> Profile:
    profile_file = directory / "profile.toml"
    try:
        with profile_file.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PakemError(f"missing profile.toml in {directory}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {profile_file}: {exc}") from exc

    for key in ("name", "version", "requires_framework", "digest"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise PakemError(f"{profile_file}: '{key}' must be a non-empty string")

    name = data["name"]
    if not PROFILE_NAME_PATTERN.match(name):
        raise PakemError(f"{profile_file}: invalid profile name {name!r}")
    version_tuple(data["version"])

    if not version_satisfies(framework_version, data["requires_framework"]):
        raise PakemError(
            f"profile {name} requires framework {data['requires_framework']}, "
            f"but this framework is {framework_version}"
        )

    digest_path = _resolve_inside(directory, data["digest"], "digest")
    if not digest_path.is_file():
        raise PakemError(f"profile {name}: digest file not found: {data['digest']}")

    skills_dir: Path | None = None
    raw_skills_dir = data.get("skills_dir")
    if raw_skills_dir is not None:
        if not isinstance(raw_skills_dir, str) or not raw_skills_dir:
            raise PakemError(f"{profile_file}: 'skills_dir' must be a non-empty string")
        skills_dir = _resolve_inside(directory, raw_skills_dir, "skills_dir")
        if not skills_dir.is_dir():
            raise PakemError(f"profile {name}: skills_dir not found: {raw_skills_dir}")

    return Profile(
        name=name,
        version=data["version"],
        root=directory.resolve(),
        digest_path=digest_path,
        skills_dir=skills_dir,
    )


def load_profiles(directories: list[str] | None, framework_version: str) -> list[Profile]:
    profiles: list[Profile] = []
    seen_names: set[str] = set()
    for raw in directories or []:
        profile = load_profile(Path(raw).expanduser(), framework_version)
        if profile.name in seen_names:
            raise PakemError(f"profile {profile.name} given more than once")
        seen_names.add(profile.name)
        profiles.append(profile)
    return profiles
