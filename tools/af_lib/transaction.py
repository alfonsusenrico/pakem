"""Undo log for one `af install --apply` run.

Before a target is written or removed, its current state is recorded: an existing
file, directory, or symlink is renamed to a sibling `.<name>.af-rollback-<n>` (an
atomic rename that keeps symlinks as links), and a missing target records the
topmost parent directory the run is about to create. On failure every recorded
target is put back in reverse order; on success the set-aside copies are deleted.
Backups recorded in the manifest are a separate, durable copy taken by the caller.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .common import display_path

ROLLBACK_SUFFIX = ".af-rollback"


@dataclass
class _Record:
    target: Path
    set_aside: Path | None
    created_root: Path | None


def _occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _free_sibling(target: Path) -> Path:
    # A leftover from an interrupted run may hold the only copy of an earlier
    # original, so it is never reused or overwritten.
    counter = 1
    while True:
        candidate = target.with_name(f".{target.name}{ROLLBACK_SUFFIX}-{counter}")
        if not _occupied(candidate):
            return candidate
        counter += 1


def _topmost_missing_parent(target: Path) -> Path | None:
    missing: Path | None = None
    current = target.parent
    while not _occupied(current) and current.parent != current:
        missing = current
        current = current.parent
    return missing


class WriteJournal:
    def __init__(self) -> None:
        self._records: list[_Record] = []

    def prepare(self, target: Path) -> None:
        """Record target's current state; an existing target is moved aside."""
        if _occupied(target):
            set_aside = _free_sibling(target)
            os.replace(target, set_aside)
            self._records.append(_Record(target, set_aside, None))
            return
        self._records.append(_Record(target, None, _topmost_missing_parent(target)))

    def roll_back(self) -> tuple[list[str], list[str]]:
        """Undo every recorded change; returns (what was rolled back, what could not be)."""
        done: list[str] = []
        problems: list[str] = []
        for record in reversed(self._records):
            shown = display_path(record.target)
            try:
                existed = _occupied(record.target)
                if existed:
                    _remove(record.target)
                if record.set_aside is not None:
                    os.replace(record.set_aside, record.target)
                    done.append(f"restored {shown}")
                elif existed:
                    done.append(f"removed {shown} (created in this run)")
                if record.created_root is not None:
                    done += self._remove_created_parents(record)
            except OSError as exc:
                detail = f"could not roll back {shown}: {type(exc).__name__}: {exc}"
                if record.set_aside is not None and _occupied(record.set_aside):
                    detail += f"; the original is kept at {display_path(record.set_aside)}"
                problems.append(detail)
        self._records = []
        return done, problems

    @staticmethod
    def _remove_created_parents(record: _Record) -> list[str]:
        """Remove now-empty directories this run created above the target (rmdir only)."""
        removed: list[str] = []
        root = record.created_root
        current = record.target.parent
        while root is not None and current.is_relative_to(root):
            if not current.is_dir() or any(current.iterdir()):
                break
            current.rmdir()
            removed.append(f"removed directory {display_path(current)} (created in this run)")
            if current == root:
                break
            current = current.parent
        return removed

    def commit(self) -> list[str]:
        """Delete the set-aside originals; returns paths that could not be deleted."""
        leftovers: list[str] = []
        for record in self._records:
            if record.set_aside is None or not _occupied(record.set_aside):
                continue
            try:
                _remove(record.set_aside)
            except OSError as exc:
                leftovers.append(f"{display_path(record.set_aside)} ({type(exc).__name__}: {exc})")
        self._records = []
        return leftovers
