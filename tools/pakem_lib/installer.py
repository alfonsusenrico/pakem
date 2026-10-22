"""`pakem install` and `pakem uninstall`: place the kernel and skills into host locations."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from .common import (
    PakemError,
    Framework,
    Report,
    display_path,
    expand_user_path,
    home_dir,
    is_git_work_tree,
    sha256_bytes,
    sha256_file,
    tree_sha,
    utc_now_iso,
    write_text_atomically,
)
from .hosts import HostTarget, load_targets, select_hosts
from .kernel import render_kernel, size_violations
from .legacy_install import migrate
from .manifest import BackupSession, entries_by_path, load_manifest, save_manifest
from .profiles import Profile, load_profiles
from .schedule import ScheduleContext, default_context
from .source import (
    WAIVER_DIRTY,
    WAIVER_UNMERGED,
    framework_source_status,
    has_uncommitted_changes,
    profile_source_findings,
    resolve_commit,
)
from .transaction import WriteJournal

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
REMOVE_MANAGED = "remove-managed"
KEPT = "kept"

REMOVE = "remove"
BACKUP_AND_REMOVE = "backup-and-remove-edited"
ALREADY_ABSENT = "already-absent"
RESTORE_FILE = "restore-backup"
RESTORE_SYMLINK = "restore-symlink"

BACKUP_REASON_UNMANAGED = "unmanaged"
BACKUP_REASON_SYMLINK = "symlink"
BACKUP_REASON_EDITED = "locally-edited"

SKILL_LOCK_RELATIVE = ".agents/.skill-lock.json"
PROFILE_SOURCE_PREFIX = "profile:"


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
    profiles: list[Profile] = field(default_factory=list)


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
            label = f"{PROFILE_SOURCE_PREFIX}{profile.name}/{directory.name}"
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
        item.detail = "content differs from what pakem installed; use --force to back up and replace"


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
        item.detail = f"existing symlink to {os.readlink(path)} was not installed by pakem"
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
        item.detail = "existing skill directory was not installed by pakem"
        return
    if current == entry.get("sha256"):
        item.action = REPLACE_MANAGED
        return
    if force:
        item.action = BACKUP_EDITED
    else:
        item.action = BLOCKED_EDITED
        item.detail = "skill differs from what pakem installed; use --force to back up and replace"


def profile_of_source(source: str) -> str | None:
    """Profile name in a manifest source label such as 'profile:work/skill'; None for framework skills."""
    if not source.startswith(PROFILE_SOURCE_PREFIX):
        return None
    return source[len(PROFILE_SOURCE_PREFIX) :].split("/", 1)[0]


def split_orphaned_skill_entries(
    entries: list[dict], available_sources: set[str], given_profiles: set[str]
) -> tuple[list[dict], list[dict]]:
    """Skill entries whose recorded source no longer exists, as (orphaned, kept).

    A profile skill counts as orphaned only when its profile was given; otherwise its
    source cannot be checked and the entry is kept.
    """
    orphaned: list[dict] = []
    kept: list[dict] = []
    for entry in entries:
        if entry.get("kind") != KIND_SKILL or entry.get("source") in available_sources:
            continue
        profile = profile_of_source(entry.get("source", ""))
        if profile is not None and profile not in given_profiles:
            kept.append(entry)
        else:
            orphaned.append(entry)
    return orphaned, kept


def _orphan_item(entry: dict, action: str, detail: str) -> InstallItem:
    return InstallItem(
        kind=KIND_SKILL,
        path=expand_user_path(entry["path"]),
        hosts=entry.get("host", "").split(","),
        source=entry.get("source", "unknown"),
        desired_sha=entry.get("sha256", ""),
        action=action,
        detail=detail,
    )


def _plan_orphan_removal(entry: dict, force: bool) -> InstallItem:
    item = _orphan_item(entry, REMOVE_MANAGED, "source no longer exists")
    path = item.path
    if not path.exists() and not path.is_symlink():
        item.action = ALREADY_ABSENT
        item.detail = "source no longer exists; the manifest entry is dropped"
    elif path.is_symlink():
        item.action = BLOCKED_EDITED
        item.detail = "source no longer exists, but the installed skill was replaced by a symlink"
    elif not path.is_dir():
        item.action = BLOCKED_EDITED
        item.detail = "source no longer exists, but the installed skill is no longer a directory"
    elif installed_sha(path, KIND_SKILL) != entry.get("sha256"):
        if force:
            item.action = BACKUP_AND_REMOVE
            item.detail = "source no longer exists; the edited copy is backed up first"
        else:
            item.action = BLOCKED_EDITED
            item.detail = (
                "source no longer exists, but the installed copy was edited since install; "
                "use --force to back up and remove"
            )
    return item


def _plan_orphans(
    plan: InstallPlan,
    manifest_entries: dict[str, dict],
    target_dirs: set[Path],
    skill_sources: list[SkillSource],
    force: bool,
) -> None:
    """Remove skills pakem installed whose source is gone; only in the selected hosts' skills dirs."""
    planned = {display_path(item.path) for item in plan.items}
    candidates = [
        entry
        for path, entry in manifest_entries.items()
        if path not in planned and expand_user_path(path).parent in target_dirs
    ]
    available = {source.label for source in skill_sources}
    given_profiles = {profile.name for profile in plan.profiles}
    orphaned, kept = split_orphaned_skill_entries(candidates, available, given_profiles)
    for entry in orphaned:
        plan.items.append(_plan_orphan_removal(entry, force))
    for entry in kept:
        plan.items.append(_orphan_item(entry, KEPT, "profile not given"))


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

    plan.profiles = load_profiles(profile_dirs, version)
    skill_sources = framework_skill_sources(framework, plan.report)
    skill_sources += profile_skill_sources(plan.profiles, plan.report)
    _reject_duplicate_skill_names(skill_sources)
    locked_names = read_skill_lock_names(plan.report)

    skill_targets = _skill_targets(hosts)
    for directory, host_ids in skill_targets.items():
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

    _plan_orphans(plan, manifest_entries, set(skill_targets), skill_sources, force)
    return plan


def _reject_duplicate_skill_names(sources: list[SkillSource]) -> None:
    seen: dict[str, str] = {}
    for source in sources:
        if source.name in seen:
            raise PakemError(
                f"skill name '{source.name}' is provided by both {seen[source.name]} and {source.label}"
            )
        seen[source.name] = source.label


def check_install_sources(plan: InstallPlan, framework: Framework, args) -> str:
    """Refuse --apply from an unmerged or dirty source unless waived; a dry run only warns.

    The rules and waivers are described in source.py. Returns a one-line source summary.
    """
    status = framework_source_status(framework.root)
    waived = {WAIVER_DIRTY: args.allow_dirty, WAIVER_UNMERGED: args.allow_unmerged}
    refusals: list[str] = []
    for finding in status.findings:
        if waived[finding.waiver]:
            plan.report.warn(f"{finding.message} (allowed by {finding.waiver})")
        elif args.apply:
            refusals.append(f"{finding.message} (pass {finding.waiver} to install anyway)")
        else:
            plan.report.warn(f"{finding.message}; --apply would refuse without {finding.waiver}")

    for profile in plan.profiles:
        for finding in profile_source_findings(profile.name, profile.root):
            if not args.strict_source:
                plan.report.warn(finding.message)
            elif args.apply:
                refusals.append(f"{finding.message} (--strict-source)")
            else:
                plan.report.warn(f"{finding.message}; --apply would refuse because of --strict-source")

    if refusals:
        listed = "\n  ".join(refusals)
        raise PakemError(f"refusing to install from this source:\n  {listed}")
    return f"framework {status.summary}"


def print_plan(title: str, items: list[InstallItem], note: str | None = None) -> None:
    print(title)
    if note:
        print(f"Source: {note}")
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
        raise PakemError(f"internal error: skill item without a source directory: {item.path}")
    item.path.parent.mkdir(parents=True, exist_ok=True)
    staging = item.path.with_name(f".{item.path.name}.pakem-staging")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(item.source_dir, staging, ignore=shutil.ignore_patterns(".DS_Store"))
        os.replace(staging, item.path)
    except OSError:
        if staging.exists():
            shutil.rmtree(staging)
        raise


BACKUP_REASONS = {
    BACKUP_UNMANAGED: BACKUP_REASON_UNMANAGED,
    BACKUP_SYMLINK: BACKUP_REASON_SYMLINK,
    BACKUP_EDITED: BACKUP_REASON_EDITED,
    BACKUP_AND_REMOVE: BACKUP_REASON_EDITED,
}
REMOVAL_ACTIONS = {REMOVE_MANAGED, BACKUP_AND_REMOVE, ALREADY_ABSENT}
NO_WRITE_ACTIONS = {UNCHANGED, KEPT, ALREADY_ABSENT}


def _manifest_entry(item: InstallItem) -> dict:
    return {
        "path": display_path(item.path),
        "kind": item.kind,
        "sha256": item.desired_sha,
        "host": ",".join(item.hosts),
        "source": item.source,
    }


def _apply_item(
    item: InstallItem, backups: BackupSession, journal: WriteJournal, new_backups: list[dict]
) -> None:
    if item.action in BACKUP_REASONS:
        new_backups.append(backups.back_up(item.path, BACKUP_REASONS[item.action]))
    journal.prepare(item.path)
    if item.action in REMOVAL_ACTIONS:
        return
    if item.kind == KIND_INSTRUCTIONS:
        _write_instruction(item)
    else:
        _write_skill(item)


def _roll_back_and_raise(
    journal: WriteJournal, backups: BackupSession, failed: InstallItem | None, exc: Exception
) -> NoReturn:
    done, problems = journal.roll_back()
    where = display_path(failed.path) if failed is not None else "the manifest"
    print(f"Install failed while writing {where}: {type(exc).__name__}: {exc}")
    print("Rolled back:")
    if not done and not problems:
        print("  (nothing had been written)")
    for line in done:
        print(f"  {line}")
    for problem in problems:
        print(f"  ROLLBACK FAILED: {problem}")
    if backups.directory is not None:
        print(
            f"Backups taken in this run remain in {display_path(backups.directory)} "
            "(not recorded in the manifest)."
        )
    print("Manifest unchanged.")
    if problems:
        outcome = f"rollback incomplete ({len(problems)} problem(s) listed above)"
    else:
        outcome = f"rolled back {len(done)} change(s)"
    raise PakemError(
        f"install failed while writing {where} ({type(exc).__name__}); {outcome}; manifest unchanged"
    ) from exc


def install_provenance(framework: Framework, plan: InstallPlan, hosts_option: str | None) -> dict:
    """What `pakem status` compares and `pakem update` repeats: the commit and the options."""
    in_git = is_git_work_tree(framework.root)
    return {
        "framework_commit": resolve_commit(framework.root, "HEAD") if in_git else None,
        "framework_clean": in_git and not has_uncommitted_changes(framework.root),
        "framework_root": str(framework.root.resolve()),
        "install_options": {
            "hosts": hosts_option,
            "profiles": [str(profile.root) for profile in plan.profiles],
        },
        "profiles": [{"name": profile.name, "version": profile.version} for profile in plan.profiles],
    }


def apply_install_plan(framework: Framework, plan: InstallPlan, provenance: dict) -> list[str]:
    """Apply the plan as one transaction; returns set-aside originals that could not be deleted.

    If any write fails, every target written in this run is restored and the manifest
    is left as it was.
    """
    manifest = load_manifest()
    entries = entries_by_path(manifest)
    backups = BackupSession()
    journal = WriteJournal()
    new_backups: list[dict] = []
    current: InstallItem | None = None
    try:
        for item in plan.items:
            if item.action == KEPT:
                continue
            current = item
            if item.action not in NO_WRITE_ACTIONS:
                _apply_item(item, backups, journal, new_backups)
            if item.action in REMOVAL_ACTIONS:
                entries.pop(display_path(item.path), None)
            else:
                entries[display_path(item.path)] = _manifest_entry(item)
        current = None
        manifest["entries"] = list(entries.values())
        manifest["backups"].extend(new_backups)
        manifest["framework_version"] = framework.version()
        manifest["installed_at"] = utc_now_iso()
        manifest.update(provenance)
        save_manifest(manifest)
    except (OSError, PakemError) as exc:
        _roll_back_and_raise(journal, backups, current, exc)
    return journal.commit()


def run_install(args, schedule_context: ScheduleContext | None = None) -> int:
    framework = Framework(Path(args.root))
    targets = load_targets(framework.targets_path)
    hosts = select_hosts(targets, args.hosts)
    plan = build_install_plan(framework, hosts, args.profile, args.force)
    source_summary = check_install_sources(plan, framework, args)

    mode = "apply" if args.apply else "dry-run"
    print_plan(f"pakem install ({mode}) framework v{framework.version()}", plan.items, source_summary)

    blocked = [item for item in plan.items if item.blocked]
    for item in blocked:
        plan.report.error(f"{item.action}: {display_path(item.path)} -- {item.detail}")

    if plan.report.errors:
        plan.report.print()
        print("Nothing written.")
        return 1

    # After every refusal above, so a refused install writes nothing at all.
    migration = migrate(framework, args.apply, schedule_context or default_context())
    migration.print()
    for warning in migration.warnings:
        plan.report.warn(warning)

    if not args.apply:
        plan.report.print()
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    leftovers = apply_install_plan(framework, plan, install_provenance(framework, plan, args.hosts))
    for leftover in leftovers:
        plan.report.warn(f"could not delete the set-aside original {leftover}; remove it by hand")
    plan.report.print()
    unchanged = [item for item in plan.items if item.action == UNCHANGED]
    kept = [item for item in plan.items if item.action == KEPT]
    changed = len(plan.items) - len(unchanged) - len(kept)
    print(f"Applied: {changed} change(s), {len(unchanged)} unchanged, {len(kept)} kept.")
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
    """Most recent backup of the pre-pakem state; backups of locally edited pakem files are skipped."""
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
        raise PakemError(f"cannot restore {display_path(item.path)}: path is occupied")
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
    print(f"pakem uninstall ({mode})")
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
