"""`pakem report`: a read-only Markdown drift report over the framework, installed state, and repos.

The report reuses the checks behind `pakem lint`, `pakem doctor`, and `pakem project check` (which
`pakem doctor --repos` runs per repository). It never fetches, never changes a checked
repository or the installed state, and records only findings, never file contents.
Its only writes are the report files in the output directory and pruning of older
reports there.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import (
    PakemError,
    Framework,
    Report,
    display_path,
    expand_user_path,
    is_git_work_tree,
    write_text_atomically,
)
from .doctor import discover_repos, installed_state_report, repo_status, unusable_repo_message
from .legacy_install import LEGACY_REPORT_CONFIG, REPORT_CONFIG
from .lint import Linter
from .manifest import reports_dir
from .project import check_repo
from .source import framework_source_status

DEFAULT_CONFIG = REPORT_CONFIG
DEFAULT_KEEP = 12
CONFIG_KEYS = ("repos_dirs", "repos", "profiles", "keep")
REPORT_NAME_FORMAT = "%Y-%m-%d-%H%M"
REPORT_NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.md$")
LATEST_NAME = "latest.md"
EXAMPLE_CONFIG = """\
repos_dirs = ["~/projects/org"]          # each direct child git repo is checked
repos = ["~/projects/personal-site"]     # optional: individual repos
profiles = ["~/projects/org/agent-profile-org"]  # optional: profile dirs for lint and doctor
keep = 12                                # optional: reports to keep
"""


@dataclass(frozen=True)
class ReportConfig:
    path: Path
    repos_dirs: list[Path]
    repos: list[Path]
    profiles: list[Path]
    keep: int


@dataclass
class Section:
    """One checked scope: a heading for the summary table and the findings behind it."""

    title: str
    report: Report


def _string_paths(data: dict, key: str, config_path: Path) -> list[Path]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PakemError(f"{config_path}: '{key}' must be a list of non-empty path strings")
    paths = []
    for raw in value:
        try:
            paths.append(expand_user_path(raw))
        except PakemError as exc:
            raise PakemError(f"{config_path}: '{key}': {exc}") from exc
    return paths


def load_config(path: Path) -> ReportConfig:
    if not path.is_file():
        raise PakemError(
            f"report config not found: {display_path(path)}\n"
            "Create it (a template is docs/report.example.toml in the framework), for example:\n\n"
            + EXAMPLE_CONFIG
        )
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {path}: {exc}") from exc

    unknown = sorted(set(data) - set(CONFIG_KEYS))
    if unknown:
        raise PakemError(f"{path}: unknown key(s) {', '.join(unknown)} (known: {', '.join(CONFIG_KEYS)})")
    if "repos_dirs" not in data:
        raise PakemError(f"{path}: 'repos_dirs' is required (it may be an empty list)")

    keep = data.get("keep", DEFAULT_KEEP)
    # bool is a subclass of int in Python, so it is rejected explicitly.
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise PakemError(f"{path}: 'keep' must be a whole number of at least 1")

    return ReportConfig(
        path=path,
        repos_dirs=_string_paths(data, "repos_dirs", path),
        repos=_string_paths(data, "repos", path),
        profiles=_string_paths(data, "profiles", path),
        keep=keep,
    )


def collect_repos(config: ReportConfig, configuration: Report) -> list[Path]:
    """Repos from repos_dirs and repos, in order, without duplicates; problems go to configuration."""
    repos: list[Path] = []
    for directory in config.repos_dirs:
        if not directory.is_dir():
            configuration.error(f"repos_dirs entry not found: {display_path(directory)}")
            continue
        found = discover_repos([str(directory)])
        if not found:
            configuration.warn(f"no git repositories found in {display_path(directory)}")
        repos.extend(found)
    for raw in config.repos:
        repo = raw.resolve()
        if not repo.is_dir():
            configuration.error(f"repo not found: {display_path(raw)}")
            continue
        repos.append(repo)

    unique: list[Path] = []
    for repo in repos:
        if repo not in unique:
            unique.append(repo)
    return unique


def source_section(framework: Framework) -> tuple[Section, str]:
    status = framework_source_status(framework.root)
    report = Report()
    for finding in status.findings:
        report.warn(finding.message)
    return Section("Framework source", report), status.summary


def lint_section(framework: Framework, profile_dirs: list[str]) -> Section:
    try:
        report = Linter(framework, profile_dirs).run()
    except PakemError as exc:
        report = Report()
        report.error(f"pakem lint could not run: {exc}")
    return Section("pakem lint", report)


def installed_state_section(framework: Framework, profile_dirs: list[str]) -> Section:
    try:
        report = installed_state_report(framework, profile_dirs)
    except PakemError as exc:
        report = Report()
        report.error(f"pakem doctor could not run: {exc}")
    return Section("pakem doctor: installed state", report)


def repo_section(framework: Framework, repo: Path) -> Section:
    title = display_path(repo)
    if not is_git_work_tree(repo):
        report = Report()
        report.warn(unusable_repo_message(repo))
        return Section(title, report)
    try:
        report = check_repo(framework, repo)
    except PakemError as exc:
        report = Report()
        report.error(f"pakem project check could not run: {exc}")
    return Section(title, report)


def _table_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _findings_block(section: Section) -> list[str]:
    lines = ["```text"]
    lines += [f"ERROR: {message}" for message in section.report.errors]
    lines += [f"WARNING: {message}" for message in section.report.warnings]
    lines.append(f"{len(section.report.errors)} error(s), {len(section.report.warnings)} warning(s)")
    lines.append("```")
    return lines


def render_report(
    generated: datetime,
    framework: Framework,
    source_summary: str,
    config: ReportConfig,
    overview: list[Section],
    repos: list[Section],
) -> str:
    try:
        version = framework.version()
    except PakemError as exc:
        version = f"unknown ({exc})"
    lines = [
        "# Pakem drift report",
        "",
        f"- Generated: {generated.strftime('%Y-%m-%d %H:%M %z')}",
        f"- Framework: `{display_path(framework.root)}` v{version}, {source_summary}",
        f"- Config: `{display_path(config.path)}`",
        "- Read-only: no fetch was made and nothing was changed.",
        "",
        "## Summary",
        "",
        "| Scope | Status | Errors | Warnings |",
        "| --- | --- | --- | --- |",
    ]
    for section in [*overview, *repos]:
        lines.append(
            f"| {_table_cell(section.title)} | {repo_status(section.report)} "
            f"| {len(section.report.errors)} | {len(section.report.warnings)} |"
        )
    for section in overview:
        lines += ["", f"## {section.title}", ""]
        lines += _findings_block(section)
    lines += ["", f"## pakem doctor: project repos ({len(repos)})", ""]
    lines.append("Each repository is checked with `pakem project check`, as `pakem doctor --repos` does.")
    for section in repos:
        lines += ["", f"### {section.title}", ""]
        lines += _findings_block(section)
    return "\n".join(lines) + "\n"


def prune_reports(directory: Path, keep: int) -> list[Path]:
    """Delete the oldest dated reports beyond keep; returns the removed paths."""
    reports = []
    for path in directory.iterdir():
        if REPORT_NAME_PATTERN.match(path.name) and path.is_file() and not path.is_symlink():
            reports.append(path)
    # Names are zero-padded local timestamps, so name order is chronological order.
    reports.sort(key=lambda path: path.name)
    removed = reports[: max(len(reports) - keep, 0)]
    for path in removed:
        path.unlink()
    return removed


def write_report(directory: Path, name: str, text: str) -> Path:
    report_path = directory / name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        write_text_atomically(report_path, text)
        write_text_atomically(directory / LATEST_NAME, text)
    except OSError as exc:
        raise PakemError(f"could not write the report to {directory}: {exc}") from exc
    return report_path


def run_report(args) -> int:
    framework = Framework(Path(args.root))
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
    else:
        config_path = expand_user_path(DEFAULT_CONFIG)
        legacy_config = expand_user_path(LEGACY_REPORT_CONFIG)
        # Never moved for the owner; the installed-state section says to move it.
        if not config_path.exists() and legacy_config.is_file():
            config_path = legacy_config
    config = load_config(config_path)
    keep = args.keep if args.keep is not None else config.keep
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else reports_dir()

    generated = datetime.now().astimezone()
    profile_dirs = [str(path) for path in config.profiles]
    source, source_summary = source_section(framework)
    configuration = Section("Configuration", Report())
    repo_paths = collect_repos(config, configuration.report)
    overview = [
        source,
        lint_section(framework, profile_dirs),
        installed_state_section(framework, profile_dirs),
        configuration,
    ]
    repos = [repo_section(framework, repo) for repo in repo_paths]
    text = render_report(generated, framework, source_summary, config, overview, repos)

    report_path = write_report(output_dir, generated.strftime(REPORT_NAME_FORMAT) + ".md", text)
    errors = sum(len(section.report.errors) for section in [*overview, *repos])
    warnings = sum(len(section.report.warnings) for section in [*overview, *repos])
    print(f"pakem report: {len(repos)} repo(s), {errors} error(s), {warnings} warning(s)")
    print(report_path)
    try:
        removed = prune_reports(output_dir, keep)
    except OSError as exc:
        print(f"WARNING: could not prune old reports in {output_dir}: {exc}")
        return 0
    if removed:
        print(f"Pruned {len(removed)} old report(s) (keep {keep}).")
    return 0
