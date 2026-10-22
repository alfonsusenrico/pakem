"""A service project's own deploy files, as `pakem setup host` and `pakem release` read them,
and the receiver command that lets a device release it on a host (ADR 0010)."""

from __future__ import annotations

import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import device_key
from .common import PakemError, display_path
from .requirements import device_label

STAGING = "staging"
PRODUCTION = "production"
INSTALLED_RECEIVER = "/opt/af/af_receive.py"


@dataclass
class Project:
    root: Path
    name: str
    environments: list[str]
    placement: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)

    @property
    def has_staging(self) -> bool:
        return STAGING in self.environments


def read_project(directory: Path) -> Project:
    root = directory.resolve()
    release_file = root / "deploy" / "release.toml"
    settings_file = root / "deploy" / "settings.toml"
    if not release_file.is_file():
        raise PakemError(f"{display_path(root)} is not a service project: it has no deploy/release.toml")
    try:
        config = tomllib.loads(release_file.read_text(encoding="utf-8"))
        settings = tomllib.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in the project's deploy files: {exc}") from exc
    name = config.get("project")
    environments = sorted(config.get("environments") or {})
    if not isinstance(name, str) or not environments:
        raise PakemError("deploy/release.toml needs project and at least one [environments.<name>]")
    return Project(root, name, environments, list(settings.get("placement", [])), list(settings.get("secrets", [])))


def is_service_project(directory: Path) -> bool:
    return (directory / "deploy" / "release.toml").is_file()


def authorize_arguments(project: Project, deploy_root: str, proxy: bool, switch_from_registry: bool) -> list[str]:
    """`af_receive.py authorize` as root: this device's key may release the project there."""
    key = " ".join(device_key.public_path().read_text(encoding="utf-8").split()[:2])
    arguments = [
        "sudo", "python3", INSTALLED_RECEIVER, "authorize", "--device", device_label(), "--key", key,
        "--project", project.name, "--deploy-root", deploy_root,
    ]
    for environment in project.environments:
        arguments += ["--environment", environment]
    for name in project.placement:
        arguments += ["--placement", name]
    for name in project.secrets:
        arguments += ["--secret", name]
    if proxy:
        arguments.append("--proxy")
    if switch_from_registry:
        arguments.append("--switch-from-registry")
    return arguments


def setup_words(target: str, flags: list[str], project_roots: list[Path]) -> str:
    """The one owner command that prepares a host and lets this device release projects."""
    words = list(flags)
    for root in project_roots:
        words += ["--project-dir", str(root)]
    # The target is a recorded host or the <ssh-target> placeholder, left unquoted to edit.
    return " ".join(["pakem setup host", target, shlex.join(words)]).rstrip()
