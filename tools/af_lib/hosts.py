"""Host target definitions loaded from hosts/targets.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import AfError, expand_user_path


@dataclass(frozen=True)
class HostTarget:
    host_id: str
    label: str
    instructions: Path
    skills_dirs: tuple[Path, ...]
    max_chars: int | None
    max_bytes: int | None
    must_not_exist: tuple[Path, ...]


def _require_string(table: dict, key: str, host_id: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise AfError(f"hosts.{host_id}.{key} must be a non-empty string")
    return value


def _optional_string_list(table: dict, key: str, host_id: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AfError(f"hosts.{host_id}.{key} must be a list of strings")
    return value


def _optional_positive_int(table: dict, key: str, host_id: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AfError(f"hosts.{host_id}.{key} must be a positive integer")
    return value


def load_targets(path: Path) -> dict[str, HostTarget]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise AfError(f"missing host targets file: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AfError(f"invalid TOML in {path}: {exc}") from exc

    hosts_table = data.get("hosts")
    if not isinstance(hosts_table, dict) or not hosts_table:
        raise AfError(f"{path} must define at least one [hosts.<id>] table")

    targets: dict[str, HostTarget] = {}
    for host_id, table in hosts_table.items():
        if not isinstance(table, dict):
            raise AfError(f"hosts.{host_id} must be a table")
        skills_dirs = _optional_string_list(table, "skills_dirs", host_id)
        targets[host_id] = HostTarget(
            host_id=host_id,
            label=_require_string(table, "label", host_id),
            instructions=expand_user_path(_require_string(table, "instructions", host_id)),
            skills_dirs=tuple(expand_user_path(item) for item in skills_dirs),
            max_chars=_optional_positive_int(table, "max_chars", host_id),
            max_bytes=_optional_positive_int(table, "max_bytes", host_id),
            must_not_exist=tuple(
                expand_user_path(item) for item in _optional_string_list(table, "must_not_exist", host_id)
            ),
        )
    return targets


def select_hosts(targets: dict[str, HostTarget], requested: str | None) -> list[HostTarget]:
    if requested is None:
        return list(targets.values())
    selected: list[HostTarget] = []
    for host_id in requested.split(","):
        host_id = host_id.strip()
        if not host_id:
            continue
        if host_id not in targets:
            known = ", ".join(sorted(targets))
            raise AfError(f"unknown host {host_id!r}; known hosts: {known}")
        selected.append(targets[host_id])
    if not selected:
        raise AfError("--hosts selected no hosts")
    return selected
