"""Fetch and verify a release for the engine: pointer, record, signatures, deploy bundle.

Contracts v1, sections 1-3. Every reference is resolved to a digest before use, every
signature is checked before any content is trusted, and the bundle is unpacked with
files and directories only. Standard library plus the `cosign` executable.
"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import contracts
from af_oci import Manifest, Reference, Registry, RegistryError

RELEASE_MEDIA = "application/vnd.af.release.v1+json"
BUNDLE_MEDIA = "application/vnd.af.bundle.v1.tar"
MAX_RECORD_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_EXPANDED = 64 * 1024 * 1024
MAX_BUNDLE_ENTRIES = 2000
REQUIRED_BUNDLE_FILES = ("deploy/release.toml", "deploy/settings.toml")

Verify = Callable[[str], None]


class ReleaseError(Exception):
    """The release cannot be trusted or used; nothing has been changed."""


@dataclass(frozen=True)
class Release:
    digest: str
    record: dict


class CosignVerifier:
    """Check a signature made with the project's release signing key."""

    def __init__(
        self,
        public_key: Path,
        *,
        cosign: str = "cosign",
        transparency_log: bool = False,
        docker_config: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._public_key = public_key
        self._cosign = cosign
        self._transparency_log = transparency_log
        self._docker_config = docker_config
        self._run = run

    def __call__(self, reference: str) -> None:
        if "@sha256:" not in reference:
            raise ReleaseError("signatures are checked on digests only")
        arguments = [self._cosign, "verify", "--key", str(self._public_key)]
        if not self._transparency_log:
            arguments.append("--insecure-ignore-tlog=true")
        if reference.split("/")[0].split(":")[0] in {"127.0.0.1", "localhost"}:
            # Loopback registries (tests, rehearsals) speak plain http, like af_oci.
            arguments.append("--allow-http-registry")
        arguments.append(reference)
        environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        if self._docker_config is not None:
            environment["DOCKER_CONFIG"] = str(self._docker_config)
        result = self._run(arguments, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0:
            raise ReleaseError(f"signature check failed for {reference.split('@')[0]}")


def _single_layer(manifest: Manifest, media_type: str, label: str) -> dict:
    document = manifest.document
    artifact_type = document.get("artifactType") or document.get("config", {}).get("mediaType")
    layers = document.get("layers")
    if artifact_type != media_type or not isinstance(layers, list) or len(layers) != 1:
        raise ReleaseError(f"{label} is not a single-layer {media_type} artifact")
    layer = layers[0]
    if layer.get("mediaType") != media_type or not isinstance(layer.get("digest"), str):
        raise ReleaseError(f"{label} layer has the wrong media type")
    return layer


def fetch_release(registry: Registry, pointer: str, project: str, verify: Verify) -> Release:
    """Resolve the environment's pointer and return its verified, valid release record."""
    try:
        reference = Reference.parse(pointer)
        if reference.tag is None or reference.digest is not None:
            raise ReleaseError("the pointer must be a tag such as <image>:release-staging")
        manifest = registry.manifest(reference)
        pinned = reference.with_digest(manifest.digest)
        verify(str(pinned))
        layer = _single_layer(manifest, RELEASE_MEDIA, "release record")
        data = registry.blob(pinned, layer["digest"], MAX_RECORD_BYTES)
    except RegistryError as error:
        raise ReleaseError(str(error)) from None
    try:
        record = json.loads(data)
    except ValueError:
        raise ReleaseError("release record is not JSON") from None
    problems = contracts.validate_release_record(record)
    if problems:
        raise ReleaseError("; ".join(problems))
    if record["project"] != project:
        raise ReleaseError("release record belongs to another project")
    for target in [*record["images"].values(), record["bundle"]]:
        verify(target)
    return Release(manifest.digest, record)


def check_order(current: dict | None, candidate: dict) -> str:
    """'deploy', or 'unchanged' when this exact release already runs; refuses going back."""
    if current is None:
        return "deploy"
    if current == candidate:
        return "unchanged"
    if candidate["created"] < current["created"] and not candidate["rollback"]:
        raise ReleaseError("release is older than the one deployed and is not marked as a rollback")
    return "deploy"


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in name:
        raise ReleaseError("bundle entry escapes the bundle")
    if any(part.startswith(".env") for part in path.parts):
        raise ReleaseError("bundle contains an .env file. Settings never travel in bundles")
    return path


def unpack_bundle(archive: Path, destination: Path) -> None:
    """Unpack into an empty private directory: regular files and directories only."""
    destination.mkdir(mode=0o700)
    try:
        bundle = tarfile.open(archive, mode="r:")
    except tarfile.TarError:
        raise ReleaseError("bundle is not an uncompressed tar archive") from None
    total = 0
    seen: set[PurePosixPath] = set()
    with bundle:
        members = bundle.getmembers()
        if len(members) > MAX_BUNDLE_ENTRIES:
            raise ReleaseError("bundle has too many entries")
        for member in members:
            path = _member_path(member.name)
            if path in seen:
                raise ReleaseError("bundle repeats an entry")
            seen.add(path)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif member.isreg():
                total += member.size
                if total > MAX_BUNDLE_EXPANDED:
                    raise ReleaseError("bundle expands beyond its size limit")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ReleaseError("bundle entry cannot be read")
                mode = 0o700 if member.mode & 0o100 else 0o600
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                with source, os.fdopen(os.open(target, flags, mode), "wb") as handle:
                    while chunk := source.read(1024 * 1024):
                        handle.write(chunk)
            else:
                raise ReleaseError("bundle may contain only files and directories")


def fetch_bundle(registry: Registry, record: dict, destination: Path) -> dict:
    """Download, check, and unpack the record's bundle; returns its release.toml."""
    try:
        reference = Reference.parse(record["bundle"])
        manifest = registry.manifest(reference)
        layer = _single_layer(manifest, BUNDLE_MEDIA, "deploy bundle")
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".bundle-", delete=False) as handle:
            archive = Path(handle.name)
            registry.blob(reference, layer["digest"], MAX_BUNDLE_BYTES, output=handle)
    except RegistryError as error:
        raise ReleaseError(str(error)) from None
    try:
        unpack_bundle(archive, destination)
    finally:
        archive.unlink(missing_ok=True)
    for required in REQUIRED_BUNDLE_FILES:
        if not (destination / required).is_file():
            raise ReleaseError(f"bundle lacks {required}")
    config = tomllib.loads((destination / "deploy/release.toml").read_text(encoding="utf-8"))
    settings = tomllib.loads((destination / "deploy/settings.toml").read_text(encoding="utf-8"))
    problems = contracts.validate_project_config(config) + contracts.validate_settings_manifest(settings)
    if config.get("project") != record["project"]:
        problems.append("bundle release.toml names another project")
    if set(record["images"]) != {image["name"] for image in config.get("images", [])}:
        problems.append("record images differ from the bundle's [[images]]")
    if problems:
        raise ReleaseError("; ".join(problems))
    return config
