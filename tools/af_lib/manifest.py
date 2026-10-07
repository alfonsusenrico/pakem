"""Install manifest and backup storage under ~/.local/state/agent-framework."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .common import AfError, display_path, home_dir, utc_now_iso, utc_timestamp, write_text_atomically

MANIFEST_SCHEMA = 1
BACKUP_INDEX_NAME = "backup-index.json"


def state_dir() -> Path:
    return home_dir() / ".local" / "state" / "agent-framework"


def manifest_path() -> Path:
    return state_dir() / "manifest.json"


def shared_data_dir() -> Path:
    return home_dir() / ".local" / "share" / "agent-framework"


def empty_manifest() -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "framework_version": None,
        "installed_at": None,
        "entries": [],
        "backups": [],
        "enforcement": {},
    }


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AfError(f"manifest is not valid JSON ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise AfError(f"manifest must be a JSON object: {path}")
    manifest = empty_manifest()
    manifest.update(data)
    return manifest


def save_manifest(manifest: dict) -> None:
    write_text_atomically(manifest_path(), json.dumps(manifest, indent=2) + "\n")


def entries_by_path(manifest: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in manifest.get("entries", [])}


class BackupSession:
    """Creates one timestamped backup directory per command run, on first use."""

    def __init__(self) -> None:
        self.timestamp = utc_timestamp()
        self._directory: Path | None = None
        self._index: list[dict] = []

    def _ensure_directory(self) -> Path:
        if self._directory is not None:
            return self._directory
        base = state_dir() / "backups"
        candidate = base / self.timestamp
        suffix = 1
        while candidate.exists():
            suffix += 1
            candidate = base / f"{self.timestamp}-{suffix}"
        candidate.mkdir(parents=True)
        self._directory = candidate
        return candidate

    def _destination_for(self, original: Path) -> Path:
        directory = self._ensure_directory()
        try:
            relative = original.relative_to(home_dir())
        except ValueError:
            relative = Path("_root") / original.relative_to(original.anchor)
        return directory / relative

    def back_up(self, original: Path, reason: str) -> dict:
        """Preserve original (file, directory, or symlink) and return the manifest record."""
        record = {
            "original": display_path(original),
            "timestamp": self.timestamp,
            "reason": reason,
        }
        if original.is_symlink():
            # Record the link itself rather than copying the target's content.
            record["link_target"] = os.readlink(original)
            self._index.append(dict(record))
            self._write_index()
            return record

        destination = self._destination_for(original)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if original.is_dir():
            shutil.copytree(original, destination, symlinks=True)
        elif original.is_file():
            shutil.copy2(original, destination)
        else:
            raise AfError(f"cannot back up {original}: not a file, directory, or symlink")
        record["backup_path"] = display_path(destination)
        self._index.append(dict(record))
        self._write_index()
        return record

    def _write_index(self) -> None:
        directory = self._ensure_directory()
        index = {"created_at": utc_now_iso(), "items": self._index}
        write_text_atomically(directory / BACKUP_INDEX_NAME, json.dumps(index, indent=2) + "\n")
