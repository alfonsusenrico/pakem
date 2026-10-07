"""`af doctor`: compare what is installed with what the framework expects."""

from __future__ import annotations

from pathlib import Path

from .common import (
    AfError,
    Framework,
    Report,
    display_path,
    expand_user_path,
    is_git_work_tree,
    tree_sha,
    version_tuple,
)
from .hosts import HostTarget, load_targets
from .installer import KIND_INSTRUCTIONS, installed_sha, profile_skill_sources
from .manifest import entries_by_path, load_manifest
from .profiles import load_profiles
from .project import check_repo


def check_manifest(framework: Framework, report: Report) -> None:
    manifest = load_manifest()
    entries = manifest.get("entries", [])
    if not entries:
        report.warn("nothing installed according to the manifest (run af install)")
        return

    installed_version = manifest.get("framework_version")
    framework_version = framework.version()
    if installed_version and version_tuple(installed_version) < version_tuple(framework_version):
        report.warn(
            f"installed framework v{installed_version} is older than repo v{framework_version} (run af install)"
        )

    for entry in entries:
        path = expand_user_path(entry["path"])
        if not path.exists() and not path.is_symlink():
            report.error(f"missing: {entry['path']} ({entry['kind']}) is in the manifest but not on disk")
            continue
        if path.is_symlink():
            report.error(f"edited: {entry['path']} was replaced by a symlink")
            continue
        if entry["kind"] == KIND_INSTRUCTIONS and not path.is_file():
            report.error(f"edited: {entry['path']} is no longer a regular file")
            continue
        if installed_sha(path, entry["kind"]) != entry.get("sha256"):
            report.error(f"edited: {entry['path']} differs from what af installed")


def check_global_instruction_files(host: HostTarget, report: Report) -> None:
    candidates = [host.instructions, *host.must_not_exist]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(present) > 1:
        listed = ", ".join(display_path(path) for path in present)
        report.error(f"{host.host_id}: more than one global instruction file: {listed}")


def check_duplicate_skills(host: HostTarget, report: Report) -> None:
    locations: dict[str, list[Path]] = {}
    for directory in host.skills_dirs:
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                locations.setdefault(child.name, []).append(directory)
    for name, directories in sorted(locations.items()):
        if len(directories) > 1:
            listed = ", ".join(display_path(directory) for directory in directories)
            report.error(f"{host.host_id}: skill '{name}' is present in more than one skills dir: {listed}")


def check_profile_skills(
    framework: Framework, profile_dirs: list[str], hosts: list[HostTarget], report: Report
) -> None:
    """Every skill of the given profiles is installed, per the manifest, in every host skills dir."""
    profiles = load_profiles(profile_dirs, framework.version())
    entries = entries_by_path(load_manifest())
    skills_dirs: list[Path] = []
    for host in hosts:
        for directory in host.skills_dirs:
            if directory not in skills_dirs:
                skills_dirs.append(directory)
    for source in profile_skill_sources(profiles, report):
        source_sha = tree_sha(source.directory)
        for directory in skills_dirs:
            installed_path = display_path(directory / source.name)
            entry = entries.get(installed_path)
            if entry is None or entry.get("source") != source.label:
                report.error(f"not installed: {source.label} at {installed_path} (run af install --profile)")
            elif entry.get("sha256") != source_sha:
                report.warn(f"outdated: {installed_path} differs from {source.label} (run af install --profile)")


def discover_repos(directories: list[str]) -> list[Path]:
    repos: list[Path] = []
    for raw in directories:
        base = Path(raw).expanduser().resolve()
        if not base.is_dir():
            raise AfError(f"--repos directory not found: {base}")
        if (base / ".git").exists():
            repos.append(base)
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                repos.append(child)
    return repos


def check_repos(framework: Framework, directories: list[str], report: Report) -> None:
    repos = discover_repos(directories)
    print(f"Project repos ({len(repos)}):")
    for repo in repos:
        if not is_git_work_tree(repo):
            report.warn(f"{repo}: has .git but is not a usable work tree")
            continue
        repo_report = check_repo(framework, repo)
        status = "ok"
        if repo_report.errors:
            status = "ERRORS"
        elif repo_report.warnings:
            status = "warnings"
        print(
            f"  {status:8}  {repo}  ({len(repo_report.errors)} error(s), {len(repo_report.warnings)} warning(s))"
        )
        report.extend(repo_report, prefix=f"{repo.name}: ")


def run_doctor(args) -> int:
    framework = Framework(Path(args.root))
    report = Report()
    check_manifest(framework, report)
    hosts = list(load_targets(framework.targets_path).values())
    for host in hosts:
        check_global_instruction_files(host, report)
        check_duplicate_skills(host, report)
    if args.profile:
        check_profile_skills(framework, args.profile, hosts, report)
    if args.repos:
        check_repos(framework, args.repos, report)
    print("af doctor")
    report.print()
    return report.exit_code
