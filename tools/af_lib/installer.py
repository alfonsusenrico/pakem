"""`af install` and `af uninstall`: place the kernel and skills into host locations."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    AfError,
    Framework,
    Report,
    display_path,
    expand_user_path,
    home_dir,
    is_git_work_tree,
    run_git,
    sha256_bytes,
    sha256_file,
    tree_sha,
    utc_now_iso,
    write_text_atomically,
)
from .hosts import HostTarget, load_targets, select_hosts
from .kernel import render_kernel, size_violations
from .manifest import BackupSession, entries_by_path, load_manifest, save_manifest
from .profiles import load_profiles

KIND_INSTRUCTIONS = "instructions"
KIND_SKILL = "skill"

CREATE = "create"
UNCHANGED = "unchanged"
REPLACE_MANAGED = "replace-managed"
BACKUP_UNMANAGED = "backup-and-replace-unmanaged"
BACKUP_SYMLINK = "backup-and-replace-symlink"
BACKUP_EDITED = "backup-and-replace-edited"
BLOCKED_EDITED = "BLOCKED-locally-edited"
BLOCKED_UNMANAGED = "BLOCKED-unmanaged-skill"
BLOCKED_SKILL_LOCK = "BLOCKED-skill-lock"
BLOCKED_NOT_A_FILE = "BLOCKED-not-a-file"

REMOVE = "remove"
BACKUP_AND_REMOVE = "backup-and-remove-edited"
ALREADY_ABSENT = "already-absent"
RESTORE_FILE = "restore-backup"
RESTORE_SYMLINK = "restore-symlink"

BACKUP_REASON_UNMANAGED = "unmanaged"
BACKUP_REASON_SYMLINK = "symlink"
BACKUP_REASON_EDITED = "locally-edited"

SKILL_LOCK_RELATIVE = ".agents/.skill-lock.json"


@dataclass
class InstallItem:
    kind: str
    path: Path
    hosts: list[str]
    source: str
    desired_sha: str
    rendered_text: str | None = None
    source_dir: Path | None = None
    action: str = ""
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.action.startswith("BLOCKED")


@dataclass
class SkillSource:
    name: str
    directory: Path
    label: str


@dataclass
class InstallPlan:
    items: list[InstallItem] = field(default_factory=list)
    report: Report = field(default_factory=Report)


def installed_sha(path: Path, kind: str) -> str:
    if kind == KIND_INSTRUCTIONS:
        return sha256_file(path)
    return tree_sha(path)


def framework_skill_sources(framework: Framework, report: Report) -> list[SkillSource]:
    sources: list[SkillSource] = []
    if not framework.skills_root.is_dir():
        return sources
    for directory in sorted(framework.skills_root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if not (directory / "SKILL.md").is_file():
            report.warn(f"skills/{directory.name} has no SKILL.md; not installed")
            continue
        sources.append(SkillSource(directory.name, directory, f"skills/{directory.name}"))
    return sources


def profile_skill_sources(profiles, report: Report) -> list[SkillSource]:
    sources: list[SkillSource] = []
    for profile in profiles:
        if profile.skills_dir is None:
            continue
        for directory in sorted(profile.skills_dir.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if not (directory / "SKILL.md").is_file():
                report.warn(f"profile {profile.name}: {directory.name} has no SKILL.md; not installed")
                continue
            label = f"profile:{profile.name}/{directory.name}"
            sources.append(SkillSource(directory.name, directory, label))
    return sources


def read_skill_lock_names(report: Report) -> set[str]:
    """Skill names owned by another installer (~/.agents/.skill-lock.json)."""
    lock_path = home_dir() / SKILL_LOCK_RELATIVE
    if not lock_path.exists():
        return set()
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.warn(
            f"could not parse {display_path(lock_path)} ({type(exc).__name__}); skill-lock collisions not checked"
        )
        return set()

    names: set[str] = set()
    skills = data.get("skills") if isinstance(data, dict) else None
    if isinstance(skills, dict):
        names.update(key for key in skills if isinstance(key, str))
    elif isinstance(skills, list):
        for entry in skills:
            if isinstance(entry, str):
                names.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.add(entry["name"])
    else:
        report.warn(f"{display_path(lock_path)} has no recognizable 'skills' collection")
    return names


def _decide_instruction_action(item: InstallItem, entry: dict | None, force: bool) -> None:
    path = item.path
    if path.is_symlink():
        item.action = BACKUP_SYMLINK
        item.detail = f"link target {os.readlink(path)}"
        return
    if not path.exists():
        item.action = CREATE
        return
    if not path.is_file():
        item.action = BLOCKED_NOT_A_FILE
        item.detail = "path exists but is not a regular file"
        return
    current = installed_sha(path, item.kind)
    if current == item.desired_sha:
        item.action = UNCHANGED
        return
    if entry is None:
        item.action = BACKUP_UNMANAGED
        return
    if current == entry.get("sha256"):
        item.action = REPLACE_MANAGED
        return
    if force:
        item.action = BACKUP_EDITED
    else:
        item.action = BLOCKED_EDITED
        item.detail = "content differs from what af installed; use --force to back up and replace"


def _decide_skill_action(
    item: InstallItem, name: str, entry: dict | None, force: bool, locked_names: set[str]
) -> None:
    path = item.path
    if name in locked_names:
        item.action = BLOCKED_SKILL_LOCK
        item.detail = f"name '{name}' is managed by ~/{SKILL_LOCK_RELATIVE}"
        return
    if path.is_symlink():
        item.action = BLOCKED_UNMANAGED
        item.detail = f"existing symlink to {os.readlink(path)} was not installed by af"
        return
    if not path.exists():
        item.action = CREATE
        return
    if not path.is_dir():
        item.action = BLOCKED_UNMANAGED
        item.detail = "path exists but is not a directory"
        return
    current = installed_sha(path, item.kind)
    if current == item.desired_sha:
        item.action = UNCHANGED
        return
    if entry is None:
        item.action = BLOCKED_UNMANAGED
        item.detail = "existing skill directory was not installed by af"
        return
    if current == entry.get("sha256"):
        item.action = REPLACE_MANAGED
        return
    if force:
        item.action = BACKUP_EDITED
    else:
        item.action = BLOCKED_EDITED
        item.detail = "skill differs from what af installed; use --force to back up and replace"


def _skill_targets(hosts: list[HostTarget]) -> dict[Path, list[str]]:
    """Map each distinct skills directory to the hosts that read it."""
    directories: dict[Path, list[str]] = {}
    for host in hosts:
        for directory in host.skills_dirs:
            directories.setdefault(directory, []).append(host.host_id)
    return directories


def build_install_plan(
    framework: Framework,
    hosts: list[HostTarget],
    profile_dirs: list[str] | None,
    force: bool,
) -> InstallPlan:
    plan = InstallPlan()
    version = framework.version()
    rendered = render_kernel(framework)
    rendered_sha = sha256_bytes(rendered.encode("utf-8"))
    manifest_entries = entries_by_path(load_manifest())

    for host in hosts:
        for violation in size_violations(rendered, host):
            plan.report.error(violation)

    instruction_hosts: dict[Path, list[str]] = {}
    for host in hosts:
        instruction_hosts.setdefault(host.instructions, []).append(host.host_id)
        for forbidden in host.must_not_exist:
            if forbidden.exists() or forbidden.is_symlink():
                plan.report.warn(
                    f"{host.host_id}: {display_path(forbidden)} exists; it competes with "
                    f"{display_path(host.instructions)} as a global instruction file (not deleted)"
                )

    for path, host_ids in instruction_hosts.items():
        item = InstallItem(
            kind=KIND_INSTRUCTIONS,
            path=path,
            hosts=host_ids,
            source="kernel/KERNEL.md",
            desired_sha=rendered_sha,
            rendered_text=rendered,
        )
        _decide_instruction_action(item, manifest_entries.get(display_path(path)), force)
        plan.items.append(item)

    profiles = load_profiles(profile_dirs, version)
    skill_sources = framework_skill_sources(framework, plan.report)
    skill_sources += profile_skill_sources(profiles, plan.report)
    _reject_duplicate_skill_names(skill_sources)
    locked_names = read_skill_lock_names(plan.report)

    for directory, host_ids in _skill_targets(hosts).items():
        for source in skill_sources:
            item = InstallItem(
                kind=KIND_SKILL,
                path=directory / source.name,
                hosts=host_ids,
                source=source.label,
                desired_sha=tree_sha(source.directory),
                source_dir=source.directory,
            )
            entry = manifest_entries.get(display_path(item.path))
            _decide_skill_action(item, source.name, entry, force, locked_names)
            plan.items.append(item)
    return plan


def _reject_duplicate_skill_names(sources: list[SkillSource]) -> None:
    seen: dict[str, str] = {}
    for source in sources:
        if source.name in seen:
            raise AfError(
                f"skill name '{source.name}' is provided by both {seen[source.name]} and {source.label}"
            )
        seen[source.name] = source.label


def check_framework_clean(framework: Framework, allow_dirty: bool, apply: bool, report: Report) -> None:
    if not is_git_work_tree(framework.root):
        message = f"framework root {framework.root} is not a git work tree; cleanliness not verified"
        if apply and not allow_dirty:
            raise AfError(message + " (pass --allow-dirty to proceed)")
        report.warn(message)
        return
    status = run_git(framework.root, "status", "--porcelain").stdout
    if not status.strip():
        return
    message = "framework git tree has uncommitted changes; installed content would not match a commit"
    if apply and not allow_dirty:
        raise AfError(message + " (commit first or pass --allow-dirty)")
    report.warn(message)


def print_plan(title: str, items: list[InstallItem]) -> None:
    print(title)
    if not items:
        print("  (nothing to do)")
    width = max((len(item.action) for item in items), default=0)
    for item in items:
        line = f"  {item.action.ljust(width)}  {display_path(item.path)}  [{item.kind}; {','.join(item.hosts)}; {item.source}]"
        if item.detail:
            line += f" -- {item.detail}"
        print(line)


def _write_instruction(item: InstallItem) -> None:
    write_text_atomically(item.path, item.rendered_text or "")


def _write_skill(item: InstallItem) -> None:
    if item.source_dir is None:
        raise AfError(f"internal error: skill item without a source directory: {item.path}")
    item.path.parent.mkdir(parents=True, exist_ok=True)
    staging = item.path.with_name(f".{item.path.name}.af-staging")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(item.source_dir, staging, ignore=shutil.ignore_patterns(".DS_Store"))
    if item.path.exists() or item.path.is_symlink():
        shutil.rmtree(item.path)
    os.replace(staging, item.path)


BACKUP_REASONS = {
    BACKUP_UNMANAGED: BACKUP_REASON_UNMANAGED,
    BACKUP_SYMLINK: BACKUP_REASON_SYMLINK,
    BACKUP_EDITED: BACKUP_REASON_EDITED,
}


def apply_install_plan(framework: Framework, plan: InstallPlan) -> None:
    manifest = load_manifest()
    entries = entries_by_path(manifest)
    backups = BackupSession()

    for item in plan.items:
        if item.action in BACKUP_REASONS:
            record = backups.back_up(item.path, BACKUP_REASONS[item.action])
            manifest["backups"].append(record)
            if item.path.is_symlink() or item.path.is_file():
                item.path.unlink()
        if item.action != UNCHANGED:
            if item.kind == KIND_INSTRUCTIONS:
                _write_instruction(item)
            else:
                _write_skill(item)
        entries[display_path(item.path)] = {
            "path": display_path(item.path),
            "kind": item.kind,
            "sha256": item.desired_sha,
            "host": ",".join(item.hosts),
            "source": item.source,
        }

    manifest["entries"] = list(entries.values())
    manifest["framework_version"] = framework.version()
    manifest["installed_at"] = utc_now_iso()
    save_manifest(manifest)


def run_install(args) -> int:
    framework = Framework(Path(args.root))
    targets = load_targets(framework.targets_path)
    hosts = select_hosts(targets, args.hosts)
    plan = build_install_plan(framework, hosts, args.profile, args.force)
    check_framework_clean(framework, args.allow_dirty, args.apply, plan.report)

    mode = "apply" if args.apply else "dry-run"
    print_plan(f"af install ({mode}) framework v{framework.version()}", plan.items)

    blocked = [item for item in plan.items if item.blocked]
    for item in blocked:
        plan.report.error(f"{item.action}: {display_path(item.path)} -- {item.detail}")

    if plan.report.errors:
        plan.report.print()
        print("Nothing written.")
        return 1

    if not args.apply:
        plan.report.print()
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    apply_install_plan(framework, plan)
    plan.report.print()
    changed = [item for item in plan.items if item.action != UNCHANGED]
    print(f"Applied: {len(changed)} change(s), {len(plan.items) - len(changed)} unchanged.")
    return 0


@dataclass
class UninstallItem:
    path: Path
    kind: str
    action: str
    detail: str = ""
    backup: dict | None = None

    @property
    def blocked(self) -> bool:
        return self.action.startswith("BLOCKED")


def _latest_restorable_backup(backups: list[dict], original: str) -> dict | None:
    """Most recent backup of the pre-af state; backups of locally edited af files are skipped."""
    candidates = [
        record
        for record in backups
        if record.get("original") == original and record.get("reason") != BACKUP_REASON_EDITED
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda record: record.get("timestamp", ""))


def build_uninstall_plan(force: bool) -> tuple[list[UninstallItem], dict]:
    manifest = load_manifest()
    items: list[UninstallItem] = []
    for entry in manifest.get("entries", []):
        path = expand_user_path(entry["path"])
        kind = entry["kind"]
        if not path.exists() and not path.is_symlink():
            items.append(UninstallItem(path, kind, ALREADY_ABSENT))
        elif path.is_symlink():
            items.append(UninstallItem(path, kind, BLOCKED_EDITED, "replaced by a symlink after install"))
        elif installed_sha(path, kind) == entry.get("sha256"):
            items.append(UninstallItem(path, kind, REMOVE))
        elif force:
            items.append(UninstallItem(path, kind, BACKUP_AND_REMOVE))
        else:
            items.append(
                UninstallItem(
                    path, kind, BLOCKED_EDITED, "edited since install; use --force to back up and remove"
                )
            )

    for item in list(items):
        if item.blocked:
            continue
        backup = _latest_restorable_backup(manifest.get("backups", []), display_path(item.path))
        if backup is None:
            continue
        if "link_target" in backup:
            restore = UninstallItem(
                item.path, item.kind, RESTORE_SYMLINK, f"-> {backup['link_target']}", backup
            )
        else:
            restore = UninstallItem(
                item.path, item.kind, RESTORE_FILE, f"from {backup['backup_path']}", backup
            )
        items.append(restore)
    return items, manifest


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _restore(item: UninstallItem) -> None:
    backup = item.backup or {}
    if item.path.exists() or item.path.is_symlink():
        raise AfError(f"cannot restore {display_path(item.path)}: path is occupied")
    item.path.parent.mkdir(parents=True, exist_ok=True)
    if "link_target" in backup:
        os.symlink(backup["link_target"], item.path)
        return
    source = expand_user_path(backup["backup_path"])
    if source.is_dir():
        shutil.copytree(source, item.path, symlinks=True)
    else:
        shutil.copy2(source, item.path)


def run_uninstall(args) -> int:
    items, manifest = build_uninstall_plan(args.force)
    report = Report()
    mode = "apply" if args.apply else "dry-run"
    print(f"af uninstall ({mode})")
    if not items:
        print("  (manifest lists nothing installed)")
    width = max((len(item.action) for item in items), default=0)
    for item in items:
        line = f"  {item.action.ljust(width)}  {display_path(item.path)}  [{item.kind}]"
        if item.detail:
            line += f" -- {item.detail}"
        print(line)

    for item in items:
        if item.blocked:
            report.error(f"{item.action}: {display_path(item.path)} -- {item.detail}")
    if report.errors:
        report.print()
        print("Nothing removed.")
        return 1
    if not args.apply:
        report.print()
        print("Dry run: nothing removed. Re-run with --apply to remove.")
        return 0

    backups = BackupSession()
    removed_paths: set[str] = set()
    restored_backups: list[dict] = []
    for item in items:
        if item.action == BACKUP_AND_REMOVE:
            manifest["backups"].append(backups.back_up(item.path, BACKUP_REASON_EDITED))
            _remove_path(item.path)
        elif item.action == REMOVE:
            _remove_path(item.path)
        if item.action in (REMOVE, BACKUP_AND_REMOVE, ALREADY_ABSENT):
            removed_paths.add(display_path(item.path))
    for item in items:
        if item.action in (RESTORE_FILE, RESTORE_SYMLINK):
            _restore(item)
            restored_backups.append(item.backup)

    manifest["entries"] = [entry for entry in manifest["entries"] if entry["path"] not in removed_paths]
    manifest["backups"] = [record for record in manifest["backups"] if record not in restored_backups]
    save_manifest(manifest)
    report.print()
    print(f"Removed {len(removed_paths)} item(s); restored {len(restored_backups)} backup(s).")
    return 0
