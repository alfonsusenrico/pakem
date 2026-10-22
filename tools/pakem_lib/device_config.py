"""Device-local settings in ~/.config/pakem/config.toml (`pakem config`).

Only keys listed in SETTINGS exist; each has a fixed set of values and a default, so a
typo is refused instead of silently ignored. The file is small and written whole.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .common import PakemError, display_path, home_dir, write_text_atomically

CONFIG_NAME = "config.toml"
UPDATE_NOTIFY = "notify"
UPDATE_APPLY = "apply"
# `update apply` is the owner's standing approval for agents to run `pakem update`
# (ADR 0011, decision 7); `notify` only tells the owner.
SETTINGS = {
    "update": ((UPDATE_NOTIFY, UPDATE_APPLY), UPDATE_NOTIFY),
}


def config_dir() -> Path:
    return home_dir() / ".config" / "pakem"


def config_path() -> Path:
    return config_dir() / CONFIG_NAME


def load_config() -> dict[str, str]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    settings: dict[str, str] = {}
    for key, value in data.items():
        if key not in SETTINGS:
            raise PakemError(f"{display_path(path)}: unknown setting {key!r}")
        allowed, _ = SETTINGS[key]
        if value not in allowed:
            raise PakemError(f"{display_path(path)}: {key} must be one of {', '.join(allowed)}, not {value!r}")
        settings[key] = value
    return settings


def setting(key: str) -> str:
    _, default = SETTINGS[key]
    return load_config().get(key, default)


def _render(settings: dict[str, str]) -> str:
    lines = ["# Pakem device settings; change with `pakem config set <key> <value>`."]
    lines += [f'{key} = "{settings[key]}"' for key in sorted(settings)]
    return "\n".join(lines) + "\n"


def _require_key(key: str) -> None:
    if key not in SETTINGS:
        raise PakemError(f"unknown setting {key!r}; known: {', '.join(sorted(SETTINGS))}")


def run_config(args) -> int:
    if args.config_command == "get":
        _require_key(args.key)
        print(setting(args.key))
        return 0
    if args.config_command == "list":
        settings = load_config()
        for key in sorted(SETTINGS):
            allowed, default = SETTINGS[key]
            source = "set" if key in settings else "default"
            print(f"{key} = {settings.get(key, default)}  ({source}; one of {', '.join(allowed)})")
        return 0
    _require_key(args.key)
    allowed, _ = SETTINGS[args.key]
    if args.value not in allowed:
        raise PakemError(f"{args.key} must be one of {', '.join(allowed)}, not {args.value!r}")
    settings = load_config()
    settings[args.key] = args.value
    write_text_atomically(config_path(), _render(settings))
    print(f"{args.key} = {args.value}  ({display_path(config_path())})")
    return 0
