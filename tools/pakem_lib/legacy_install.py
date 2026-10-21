"""Upgrade a device installed under the product's former name, agent-framework.

The migration moves the state and shared-data directories to their `pakem` names,
points the guard hooks that `pakem enforce` recorded in host settings at the moved
guard, moves the recorded Codex rules file, and replaces the recorded launchd job.
Only what the manifest records as installed by this tool is changed; anything else
is reported and left alone. Every step checks its own precondition, so a second run
changes nothing and an interrupted run continues where it stopped.
"""

from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    Framework,
    PakemError,
    display_path,
    expand_user_path,
    home_dir,
    read_text,
    sha256_file,
    utc_now_iso,
    write_text_atomically,
)
from .enforce import (
    CODEX_RULES_DESTINATION,
    GUARD_MODE,
    GUARD_RECORD,
    JSON_HOSTS,
    container_at,
    guard_destination,
    load_json_object,
    render_json,
)
from .manifest import (
    BackupSession,
    LEGACY_STATE_NAME,
    STATE_NAME,
    canonical_reports_dir,
    canonical_state_dir,
    legacy_shared_data_dir,
    legacy_state_dir,
    load_manifest,
    save_manifest,
    shared_data_dir,
)
from .schedule import (
    LABEL,
    PLIST_MODE,
    SUPPORTED_PLATFORM,
    ScheduleContext,
    bootout,
    bootout_command,
    bootstrap_command,
    managed,
    plist_path,
    render_plist,
    shell_line,
)

LEGACY_GUARD_PATH_PART = f"/.local/share/{LEGACY_STATE_NAME}/"
GUARD_PATH_PART = f"/.local/share/{STATE_NAME}/"
LEGACY_HOOK_KEY = "agent-framework-guard"
HOOK_KEY = "pakem-guard"
LEGACY_CODEX_RULES = ".codex/rules/agent-framework.rules"
LEGACY_LAUNCHD_LABEL = "com.agentframework.drift-report"
LEGACY_REPORT_CONFIG = "~/.config/agent-framework/report.toml"
REPORT_CONFIG = "~/.config/pakem/report.toml"


@dataclass
class Migration:
    """What the migration did (or, in a dry run, would do), for the caller to print."""

    apply: bool
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def step(self, action: str, text: str) -> None:
        self.steps.append(f"  {action:8}  {text}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def print(self) -> None:
        """Print the steps; warnings are left to the caller's report."""
        if not self.steps:
            return
        mode = "apply" if self.apply else "dry-run"
        print(f"Migration from the former name {LEGACY_STATE_NAME} ({mode}):")
        for line in self.steps:
            print(line)


def _rename_text(text: str) -> str:
    return text.replace(LEGACY_GUARD_PATH_PART, GUARD_PATH_PART)


def _rename_value(value: object) -> object:
    """A copy of a recorded JSON value with the former guard path and hook key renamed."""
    if isinstance(value, str):
        return _rename_text(value)
    if isinstance(value, list):
        return [_rename_value(item) for item in value]
    if isinstance(value, dict):
        return {
            (HOOK_KEY if key == LEGACY_HOOK_KEY else key): _rename_value(item) for key, item in value.items()
        }
    return value


def _rename_change(change: dict) -> dict:
    renamed = copy.deepcopy(change)
    renamed["path"] = [HOOK_KEY if key == LEGACY_HOOK_KEY else key for key in change["path"]]
    # "previous" is the owner's value from before the install, so it is never renamed.
    for key in ("value", "item"):
        if key in renamed:
            renamed[key] = _rename_value(renamed[key])
    return renamed


def _rewrite_in_place(node: object) -> None:
    """Rename the former guard path inside a dict or list from a host settings file."""
    if isinstance(node, dict):
        items = node.items()
    elif isinstance(node, list):
        items = enumerate(node)
    else:
        return
    for key, value in list(items):
        if isinstance(value, str):
            node[key] = _rename_text(value)
        else:
            _rewrite_in_place(value)


def _rename_key(parent: dict, old: str, new: str) -> None:
    """Rename a key and keep its position, so the settings file reads as before."""
    renamed = {(new if key == old else key): value for key, value in parent.items()}
    parent.clear()
    parent.update(renamed)


def _move_directory(legacy: Path, canonical: Path, migration: Migration) -> bool:
    """Move legacy to canonical; True when it is (or, in a dry run, would be) moved."""
    if not legacy.is_dir() or legacy.is_symlink():
        return False
    if canonical.exists() or canonical.is_symlink():
        migration.warn(
            f"{display_path(legacy)} and {display_path(canonical)} both exist; "
            f"{display_path(legacy)} was left in place (merge it by hand)"
        )
        return False
    migration.step("move", f"{display_path(legacy)} -> {display_path(canonical)}")
    if migration.apply:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(canonical))
    return True


def _rewrite_backup_paths(manifest: dict, migration: Migration) -> bool:
    legacy_prefix = display_path(legacy_state_dir()) + "/"
    canonical_prefix = display_path(canonical_state_dir()) + "/"
    count = 0
    for record in manifest.get("backups", []):
        backup_path = record.get("backup_path", "")
        if backup_path.startswith(legacy_prefix):
            record["backup_path"] = canonical_prefix + backup_path[len(legacy_prefix) :]
            count += 1
    if count:
        where = display_path(canonical_state_dir())
        migration.step("record", f"{count} backup path(s) in the manifest point at {where}")
    return count > 0


def _migrate_guard(framework: Framework, manifest: dict, share_done: bool, migration: Migration) -> bool:
    """Point the guard record at the moved guard and refresh an unedited guard to this version.

    The refreshed guard reads the renamed protected-branch key, which `pakem hooks install`
    writes from now on.
    """
    record = manifest["enforcement"].get(GUARD_RECORD)
    legacy_guard = legacy_shared_data_dir() / "guard.py"
    if record is None or record.get("path") != display_path(legacy_guard) or not share_done:
        return False
    destination = guard_destination()
    record["path"] = display_path(destination)
    migration.step("record", f"guard at {display_path(destination)}")
    current = destination if destination.exists() else legacy_guard
    source_text = read_text(framework.enforcement_dir / "guard.py")
    if not current.is_file():
        migration.warn(f"{display_path(destination)} is missing; run pakem enforce install for each host")
        return True
    if sha256_file(current) != record.get("sha256"):
        migration.warn(f"{display_path(destination)} was edited after install; not refreshed")
        return True
    if read_text(current) != source_text:
        migration.step("refresh", f"{display_path(destination)} (this version's guard)")
        if migration.apply:
            write_text_atomically(destination, source_text, mode=GUARD_MODE)
            record["sha256"] = sha256_file(destination)
    return True


def _migrate_json_host(host: str, manifest: dict, share_done: bool, migration: Migration) -> bool:
    """Rename the recorded guard entries in one host's settings; True when the manifest changed."""
    record = manifest["enforcement"].get(host)
    if record is None:
        return False
    changes = record.get("changes", [])
    renamed = [_rename_change(change) for change in changes]
    if renamed == changes:
        return False
    target = expand_user_path(record["target"])
    if not share_done:
        migration.warn(
            f"{display_path(target)} still runs the guard from {display_path(legacy_shared_data_dir())}; "
            "it was not moved, so the hook was left as it is"
        )
        return False

    data = load_json_object(target)
    before = render_json(data)
    for change, new in zip(changes, renamed):
        if change == new:
            continue
        old_key, new_key = change["path"][-1], new["path"][-1]
        parent = container_at(data, change["path"][:-1])
        if old_key != new_key and isinstance(parent, dict) and old_key in parent:
            if new_key in parent:
                migration.warn(f"{display_path(target)}: '{new_key}' already exists; '{old_key}' left in place")
                continue
            _rename_key(parent, old_key, new_key)
            migration.step("rename", f"{display_path(target)}: key '{old_key}' -> '{new_key}'")
        node = container_at(data, new["path"])
        if isinstance(node, str):
            container = container_at(data, new["path"][:-1])
            if isinstance(container, dict):
                container[new_key] = _rename_text(node)
        else:
            _rewrite_in_place(node)
    after = render_json(data)

    if after != before:
        migration.step("rewrite", f"{display_path(target)}: guard hook runs {display_path(guard_destination())}")
        if migration.apply:
            manifest["backups"].append(BackupSession().back_up(target, f"rename-{host}"))
            write_text_atomically(target, after)
    record["changes"] = renamed
    migration.step("record", f"{host} guardrail entries under their {STATE_NAME} names")
    return True


def _migrate_codex(manifest: dict, migration: Migration) -> bool:
    record = manifest["enforcement"].get("codex")
    legacy = home_dir() / LEGACY_CODEX_RULES
    if record is None or record.get("target") != display_path(legacy):
        return False
    destination = home_dir() / CODEX_RULES_DESTINATION
    if legacy.exists() and sha256_file(legacy) != record.get("sha256"):
        migration.warn(f"{display_path(legacy)} was edited after install; left in place")
        return False
    if legacy.exists():
        if destination.exists() or destination.is_symlink():
            migration.warn(f"{display_path(destination)} already exists; {display_path(legacy)} left in place")
            return False
        migration.step("move", f"{display_path(legacy)} -> {display_path(destination)}")
        if migration.apply:
            os.replace(legacy, destination)
    record["target"] = display_path(destination)
    migration.step("record", f"codex rules at {display_path(destination)}")
    return True


def _migrate_schedule(
    framework: Framework, manifest: dict, context: ScheduleContext, migration: Migration
) -> bool:
    record = manifest["schedule"].get(LEGACY_LAUNCHD_LABEL)
    if record is None:
        return False
    legacy_plist = expand_user_path(record["plist"])
    if (legacy_plist.exists() or legacy_plist.is_symlink()) and not managed(record, legacy_plist):
        migration.warn(f"{display_path(legacy_plist)} was edited after install; launchd job left as it is")
        return False
    if context.platform != SUPPORTED_PLATFORM:
        migration.warn(f"launchd job {LEGACY_LAUNCHD_LABEL} is recorded, but launchd is not available here")
        return False

    replace_job = LABEL not in manifest["schedule"]
    destination = plist_path()
    if replace_job and (destination.exists() or destination.is_symlink()):
        migration.warn(f"{display_path(destination)} exists and was not installed by pakem; old job left as it is")
        return False

    migration.step("replace", f"launchd job {LEGACY_LAUNCHD_LABEL} -> {LABEL}")
    migration.step("run", shell_line(bootout_command(context.uid, LEGACY_LAUNCHD_LABEL)))
    migration.step("remove", display_path(legacy_plist))
    text = ""
    if replace_job:
        text = render_plist(framework, context.python, record["weekday"], record["hour"], record["minute"])
        migration.step("write", display_path(destination))
        migration.step("run", shell_line(bootstrap_command(context.uid, destination)))
    if not migration.apply:
        return True

    bootout(context, LEGACY_LAUNCHD_LABEL)
    if legacy_plist.exists() or legacy_plist.is_symlink():
        legacy_plist.unlink()
    del manifest["schedule"][LEGACY_LAUNCHD_LABEL]
    if not replace_job:
        return True
    canonical_reports_dir().mkdir(parents=True, exist_ok=True)
    write_text_atomically(destination, text, mode=PLIST_MODE)
    completed = context.runner(bootstrap_command(context.uid, destination))
    if completed.returncode != 0:
        destination.unlink()
        migration.warn(
            f"launchctl bootstrap exited {completed.returncode} ({completed.stderr.strip()}); the weekly "
            "report is not scheduled; run pakem schedule install --apply"
        )
        return True
    manifest["schedule"][LABEL] = {
        "plist": display_path(destination),
        "sha256": sha256_file(destination),
        "weekday": record["weekday"],
        "hour": record["hour"],
        "minute": record["minute"],
        "installed_at": utc_now_iso(),
    }
    return True


def legacy_report_config_notice() -> str | None:
    """A reminder while the owner's report config is still at the former path, which is never moved."""
    legacy = expand_user_path(LEGACY_REPORT_CONFIG)
    if legacy.is_file() and not expand_user_path(REPORT_CONFIG).exists():
        return (
            f"{LEGACY_REPORT_CONFIG} is at the former path; move it to {REPORT_CONFIG} "
            "(pakem report reads the former path until then)"
        )
    return None


def migrate(framework: Framework, apply: bool, context: ScheduleContext) -> Migration:
    """Migrate (or, without apply, describe migrating) this device's installed state."""
    migration = Migration(apply)
    try:
        state_moved = _move_directory(legacy_state_dir(), canonical_state_dir(), migration)
        share_moved = _move_directory(legacy_shared_data_dir(), shared_data_dir(), migration)
        # "Done" includes an earlier run that moved a directory and stopped before saving.
        state_done = state_moved or not legacy_state_dir().exists()
        share_done = share_moved or not legacy_shared_data_dir().exists()
        # A dry run reads the manifest where it still is; an applied move has already relocated it.
        manifest = load_manifest()
        changed = False
        if state_done:
            changed |= _rewrite_backup_paths(manifest, migration)
        changed |= _migrate_guard(framework, manifest, share_done, migration)
        for host in JSON_HOSTS:
            changed |= _migrate_json_host(host, manifest, share_done, migration)
        changed |= _migrate_codex(manifest, migration)
        changed |= _migrate_schedule(framework, manifest, context, migration)
        if apply and changed:
            save_manifest(manifest)
    except OSError as exc:
        raise PakemError(
            f"migration from {LEGACY_STATE_NAME} stopped: {type(exc).__name__}: {exc}; "
            "fix the cause and re-run (completed steps are not repeated)"
        ) from exc
    notice = legacy_report_config_notice()
    if notice:
        migration.warn(notice)
    return migration


def legacy_state_present() -> bool:
    """True while this device still has state under the former name."""
    return legacy_state_dir().exists() or legacy_shared_data_dir().exists()
