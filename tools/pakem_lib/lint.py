"""`pakem lint`: self-checks for the framework repository and, optionally, profile directories."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tomllib
from datetime import date
from pathlib import Path
from urllib.parse import unquote

from .capabilities import registry_problems
from .changelog import UNRELEASED_HEADING, load_fragments, unreleased_bullet_lines
from .common import PakemError, Framework, Report, display_path, read_text
from .frontmatter import parse_frontmatter
from .hosts import load_targets
from .kernel import render_kernel, size_violations
from .privacy import Scanner, private_names_path
from .profiles import Profile, load_profile

KERNEL_WARN_CHARS = 9_500
KERNEL_ERROR_CHARS = 10_500
KERNEL_WARN_LINES = 150
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
DESCRIPTION_ERROR_CHARS = 1_024
DESCRIPTION_WARN_CHARS = 350
SKILL_BODY_MAX_LINES = 500
SKILL_BODY_MAX_CHARS = 20_000
# Codex lists every installed skill's name and description in its context and trims the list
# beyond a fixed budget, so the framework keeps its own descriptions well below it.
SKILL_DESCRIPTIONS_WARN_TOTAL_CHARS = 8_000
KERNEL_SKILLS_HEADING = re.compile(r"^## (?:\d+\.\s*)?Skills\s*$")
BACKTICKED = re.compile(r"`([^`]+)`")
TEMPLATE_WARN_CHARS = 8_000
STATUS_HEADER_WINDOW = 15
STATUS_HEADER_PREFIXES = ("Status:", "Last reviewed:", "Review by:")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

EXCLUDED_TOP_LEVEL = {"archive", ".git"}
EXCLUDED_DIR_NAMES = {".git", "__pycache__", "node_modules"}
BANNED_SCAN_SUFFIXES = {".md", ".toml", ".json", ".py", ".sh", ".tmpl", ".rules"}
BANNED_EXEMPT_TOP_LEVEL = {"archive", "tests"}
PROFILE_BANNED_SCAN_SUFFIXES = BANNED_SCAN_SUFFIXES | {".yml", ".yaml"}
# Raised from 1,600 when rules moved from the kernel into profiles (ADR 0012).
PROFILE_DIGEST_WARN_CHARS = 1_800
PROFILE_DIGEST_ERROR_CHARS = 2_000
ALPHANUMERIC = re.compile(r"[A-Za-z0-9]")

INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")
INLINE_CODE = re.compile(r"`+[^`]*`+")
URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def committable_paths(root: Path) -> set[Path] | None:
    """Return files git could commit (tracked plus untracked-not-ignored), or None outside git.

    Local files excluded through .gitignore or .git/info/exclude (PROJECT_STATE.md,
    discovery notes) are never shared, so lint does not hold them to repository rules.
    """
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return {(root / name).resolve() for name in completed.stdout.split("\0") if name}


def repository_files(root: Path, excluded_top_level: set[str]) -> list[Path]:
    committable = committable_paths(root)
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        relative_dir = current.relative_to(root)
        kept = []
        for name in sorted(dir_names):
            if name in EXCLUDED_DIR_NAMES:
                continue
            if relative_dir == Path(".") and name in excluded_top_level:
                continue
            kept.append(name)
        dir_names[:] = kept
        for name in sorted(file_names):
            path = current / name
            if committable is not None and path.resolve() not in committable:
                continue
            files.append(path)
    return files


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def extract_links(text: str) -> list[tuple[int, str]]:
    """(line number, target) for markdown links outside code fences and inline code."""
    links: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        visible = INLINE_CODE.sub("", line)
        for match in INLINE_LINK.finditer(visible):
            links.append((number, match.group(1)))
        definition = REFERENCE_DEFINITION.match(visible)
        if definition is not None:
            links.append((number, definition.group(1)))
    return links


def local_link_target(target: str) -> str | None:
    """Path part of a relative link, or None for URLs, anchors, and placeholders."""
    if target.startswith("#") or target.startswith("//") or URL_SCHEME.match(target):
        return None
    if "{{" in target:
        return None
    path_part = target.split("#", 1)[0].split("?", 1)[0]
    if not path_part:
        return None
    return unquote(path_part)


def resolve_link(source: Path, link_path: str, root: Path) -> Path:
    if link_path.startswith("/"):
        return Path(os.path.normpath(root / link_path.lstrip("/")))
    return Path(os.path.normpath(source.parent / link_path))


class SkillTreeChecker:
    """Per-skill checks for every skill directory under one skills root.

    Labels are relative to label_root (the framework or profile root), which is also
    the base for root-absolute links.
    """

    def __init__(self, skills_root: Path, label_root: Path, report: Report) -> None:
        self.skills_root = skills_root
        self.label_root = label_root
        self.report = report
        self.names: list[str] = []
        self.description_chars = 0
        self.checked_files: set[Path] = set()

    def rel(self, path: Path) -> str:
        return relative_posix(path, self.label_root)

    def run(self) -> None:
        for skill_dir in sorted(self.skills_root.iterdir()):
            if skill_dir.is_dir() and not skill_dir.name.startswith("."):
                self.names.append(skill_dir.name)
                self.check_skill(skill_dir)

    def check_skill(self, skill_dir: Path) -> None:
        skill_file = skill_dir / "SKILL.md"
        label = self.rel(skill_file)
        if not skill_file.is_file():
            self.report.error(f"{self.rel(skill_dir)}: SKILL.md is missing")
            return
        parsed = parse_frontmatter(read_text(skill_file))
        for problem in parsed.errors:
            self.report.error(f"{label}: {problem}")

        name = parsed.fields.get("name")
        if name is not None:
            if not SKILL_NAME_PATTERN.match(name):
                self.report.error(f"{label}: name '{name}' must match {SKILL_NAME_PATTERN.pattern}")
            if len(name) > SKILL_NAME_MAX:
                self.report.error(f"{label}: name is longer than {SKILL_NAME_MAX} chars")
            if name != skill_dir.name:
                self.report.error(f"{label}: name '{name}' does not equal directory name '{skill_dir.name}'")

        description = parsed.fields.get("description")
        if description is not None:
            self.description_chars += len(description)
            if len(description) > DESCRIPTION_ERROR_CHARS:
                self.report.error(
                    f"{label}: description is {len(description)} chars (limit {DESCRIPTION_ERROR_CHARS})"
                )
            elif len(description) > DESCRIPTION_WARN_CHARS:
                self.report.warn(
                    f"{label}: description is {len(description)} chars (warning above {DESCRIPTION_WARN_CHARS})"
                )

        body = parsed.body
        body_lines = len(body.splitlines())
        if body_lines > SKILL_BODY_MAX_LINES:
            self.report.error(f"{label}: body is {body_lines} lines (limit {SKILL_BODY_MAX_LINES})")
        if len(body) > SKILL_BODY_MAX_CHARS:
            self.report.error(f"{label}: body is {len(body)} chars (limit {SKILL_BODY_MAX_CHARS})")

        documents = [skill_file]
        references_dir = skill_dir / "references"
        if references_dir.is_dir():
            documents += sorted(references_dir.rglob("*.md"))
        for document in documents:
            self.checked_files.add(document)
            self.check_skill_document_links(skill_dir, document, references_dir)

    def check_skill_document_links(self, skill_dir: Path, document: Path, references_dir: Path) -> None:
        label = self.rel(document)
        in_references = document.is_relative_to(references_dir)
        for number, target in extract_links(read_text(document)):
            link_path = local_link_target(target)
            if link_path is None:
                continue
            resolved = resolve_link(document, link_path, self.label_root)
            if not resolved.is_relative_to(skill_dir):
                self.report.error(f"{label}:{number}: link '{target}' leaves the skill directory")
                continue
            if not resolved.is_file():
                self.report.error(f"{label}:{number}: link '{target}' does not resolve to a file")
                continue
            if in_references and resolved.suffix == ".md" and resolved.is_relative_to(references_dir):
                self.report.warn(
                    f"{label}:{number}: reference links to another reference ({target}). Keep references one level deep"
                )


def skill_reference_documents(skills_root: Path) -> list[Path]:
    """Reference documents that need status headers (README.md files are exempt)."""
    if not skills_root.is_dir():
        return []
    return [path for path in sorted(skills_root.glob("*/references/**/*.md")) if path.name != "README.md"]


def check_status_header(path: Path, label: str, report: Report, today: date) -> None:
    head = [line.strip() for line in read_text(path).splitlines()[:STATUS_HEADER_WINDOW]]
    for prefix in STATUS_HEADER_PREFIXES:
        matching = [line for line in head if line.startswith(prefix)]
        if not matching:
            report.error(f"{label}: missing '{prefix}' line within the first {STATUS_HEADER_WINDOW} lines")
            continue
        if prefix != "Review by:":
            continue
        found = DATE_PATTERN.search(matching[0])
        if found is None:
            report.error(f"{label}: 'Review by:' needs a YYYY-MM-DD date")
            continue
        try:
            review_by = date.fromisoformat(found.group(0))
        except ValueError:
            report.error(f"{label}: 'Review by:' date {found.group(0)} is not a valid date")
            continue
        if review_by < today:
            report.error(f"{label}: review overdue (Review by: {review_by.isoformat()})")


def check_markdown_links(paths: list[Path], label_root: Path, report: Report) -> None:
    for path in paths:
        relative = relative_posix(path, label_root)
        for number, target in extract_links(read_text(path)):
            link_path = local_link_target(target)
            if link_path is None:
                continue
            if not resolve_link(path, link_path, label_root).exists():
                report.error(f"{relative}:{number}: broken link '{target}'")


def scan_banned_patterns(paths: list[Path], label_root: Path, patterns: list[dict], report: Report) -> None:
    """Report banned-pattern matches; allow_paths globs are relative to label_root."""
    for path in paths:
        relative = relative_posix(path, label_root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in patterns:
            if any(fnmatch.fnmatch(relative, glob) for glob in pattern["allow_paths"]):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern["regex"].search(line):
                    report.error(f"{relative}:{number}: [{pattern['id']}] {pattern['message']}")


class Linter:
    def __init__(self, framework: Framework, profile_dirs: list[str] | None = None) -> None:
        self.framework = framework
        self.root = framework.root
        self.report = Report()
        self.profile_dirs = profile_dirs or []
        self.framework_skills = SkillTreeChecker(framework.skills_root, self.root, self.report)
        self.banned_patterns: list[dict] = []

    def rel(self, path: Path) -> str:
        return relative_posix(path, self.root)

    def run(self) -> Report:
        self.check_kernel()
        self.check_skills()
        self.check_kernel_skill_table()
        self.check_capabilities()
        self.check_status_headers()
        self.check_markdown_links()
        self.check_banned_patterns()
        self.check_private_names()
        self.check_template()
        self.check_changelog()
        self.check_change_fragments()
        self.check_profiles()
        return self.report

    def check_kernel(self) -> None:
        path = self.framework.kernel_path
        if not path.is_file():
            self.report.error("kernel/KERNEL.md is missing")
            return
        text = read_text(path)
        char_count = len(text)
        line_count = len(text.splitlines())
        if char_count > KERNEL_ERROR_CHARS:
            self.report.error(f"kernel/KERNEL.md is {char_count} chars (limit {KERNEL_ERROR_CHARS})")
        elif char_count > KERNEL_WARN_CHARS:
            self.report.warn(f"kernel/KERNEL.md is {char_count} chars (warning above {KERNEL_WARN_CHARS})")
        if line_count > KERNEL_WARN_LINES:
            self.report.warn(f"kernel/KERNEL.md is {line_count} lines (warning above {KERNEL_WARN_LINES})")

        try:
            rendered = render_kernel(self.framework)
            targets = load_targets(self.framework.targets_path)
        except PakemError as exc:
            self.report.error(f"cannot check host size limits: {exc}")
            return
        for host in targets.values():
            for violation in size_violations(rendered, host):
                self.report.error(violation)

    def check_skills(self) -> None:
        if not self.framework.skills_root.is_dir():
            self.report.warn("skills/ directory is missing")
            return
        self.framework_skills.run()
        if self.framework_skills.description_chars > SKILL_DESCRIPTIONS_WARN_TOTAL_CHARS:
            self.report.warn(
                f"skill descriptions total {self.framework_skills.description_chars} chars "
                f"(warning above {SKILL_DESCRIPTIONS_WARN_TOTAL_CHARS}, the Codex skill-list budget)"
            )

    def kernel_skill_names(self) -> list[str]:
        """Backticked skill names in the table under the kernel's Skills heading."""
        path = self.framework.kernel_path
        if not path.is_file():
            return []
        names: list[str] = []
        in_section = False
        for line in read_text(path).splitlines():
            if line.startswith("## "):
                in_section = KERNEL_SKILLS_HEADING.match(line) is not None
                continue
            if not in_section or not line.lstrip().startswith("|"):
                continue
            for candidate in BACKTICKED.findall(line):
                if SKILL_NAME_PATTERN.match(candidate) and candidate not in names:
                    names.append(candidate)
        return names

    def check_kernel_skill_table(self) -> None:
        skills_root = self.framework.skills_root
        missing = [name for name in self.kernel_skill_names() if not (skills_root / name).is_dir()]
        if missing:
            # A warning, not an error: the kernel may name skills that are still being written.
            self.report.warn(
                "kernel/KERNEL.md skills table names skills without a skills/<name>/ directory: "
                + ", ".join(missing)
            )

    def check_capabilities(self) -> None:
        path = self.framework.capabilities_path
        label = self.rel(path)
        if not path.is_file():
            self.report.error(f"{label} is missing")
            return
        for problem in registry_problems(path):
            self.report.error(f"{label}: {problem}")

    def status_header_files(self) -> list[Path]:
        candidates = skill_reference_documents(self.framework.skills_root)
        standards = self.root / "standards"
        if standards.is_dir():
            candidates += [path for path in sorted(standards.rglob("*.md")) if path.name != "README.md"]
        return candidates

    def check_status_headers(self) -> None:
        today = date.today()
        for path in self.status_header_files():
            check_status_header(path, self.rel(path), self.report, today)

    def check_markdown_links(self) -> None:
        paths = []
        for path in repository_files(self.root, EXCLUDED_TOP_LEVEL):
            if path.suffix != ".md" or path in self.framework_skills.checked_files:
                continue
            if self.rel(path).startswith("tests/fixtures/"):
                continue
            paths.append(path)
        check_markdown_links(paths, self.root, self.report)

    def load_banned_patterns(self) -> list[dict]:
        path = self.framework.banned_patterns_path
        if not path.is_file():
            self.report.error("lint/banned.toml is missing")
            return []
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            self.report.error(f"lint/banned.toml is invalid TOML: {exc}")
            return []
        patterns = []
        for entry in data.get("banned", []):
            try:
                compiled = re.compile(entry["regex"])
            except (KeyError, re.error) as exc:
                self.report.error(f"lint/banned.toml entry {entry.get('id', '?')}: invalid regex ({exc})")
                continue
            patterns.append(
                {
                    "id": entry.get("id", "?"),
                    "regex": compiled,
                    "message": entry.get("message", ""),
                    "allow_paths": entry.get("allow_paths", []),
                }
            )
        return patterns

    def banned_scan_files(self) -> list[Path]:
        selected = []
        excluded = EXCLUDED_TOP_LEVEL | BANNED_EXEMPT_TOP_LEVEL
        for path in repository_files(self.root, excluded):
            relative = self.rel(path)
            # The pattern file necessarily contains every banned pattern.
            if relative == "lint/banned.toml":
                continue
            if path.suffix in BANNED_SCAN_SUFFIXES or relative.startswith("hooks/"):
                selected.append(path)
        return selected

    def check_banned_patterns(self) -> None:
        self.banned_patterns = self.load_banned_patterns()
        if not self.banned_patterns:
            return
        scan_banned_patterns(self.banned_scan_files(), self.root, self.banned_patterns, self.report)

    def check_private_names(self) -> None:
        """Client and private names from this device's list (never in the public tree)."""
        scanner = Scanner.for_kinds(names=True)
        if not scanner.patterns:
            return
        for path in self.banned_scan_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, _ in scanner.findings(text):
                self.report.error(f"{self.rel(path)}:{number}: [private-name] a name from "
                                  f"{display_path(private_names_path())}; use generic wording")

    def check_template(self) -> None:
        template = self.framework.project_template_path
        if not template.is_file():
            return
        char_count = len(read_text(template))
        if char_count > TEMPLATE_WARN_CHARS:
            self.report.warn(
                f"skills/project-bootstrap/assets/project/AGENTS.md.tmpl is {char_count} chars (warning above {TEMPLATE_WARN_CHARS})"
            )

    def check_changelog(self) -> None:
        path = self.framework.changelog_path
        if not path.is_file():
            return
        try:
            bullets = unreleased_bullet_lines(read_text(path))
        except PakemError as exc:
            self.report.error(str(exc))
            return
        if bullets:
            self.report.error(
                f"CHANGELOG.md line {bullets[0]}: bullet entry under {UNRELEASED_HEADING} "
                f"({len(bullets)} in total). Record changes as fragments in changes/. "
                "Only `pakem changelog --release` edits CHANGELOG.md"
            )

    def check_change_fragments(self) -> None:
        _, problems = load_fragments(self.framework.changes_dir)
        for problem in problems:
            self.report.error(problem)

    def check_profiles(self) -> None:
        if not self.profile_dirs:
            return
        profile_linters = []
        for raw in self.profile_dirs:
            profile_linter = ProfileLinter(
                Path(raw).expanduser(),
                raw,
                self.framework,
                set(self.framework_skills.names),
                self.banned_patterns,
            )
            profile_linter.run()
            self.report.extend(profile_linter.report, prefix=f"[profile {profile_linter.display_name}] ")
            profile_linters.append(profile_linter)
        self.check_profiles_together(profile_linters)

    def check_profiles_together(self, profile_linters: list["ProfileLinter"]) -> None:
        loaded = [linter for linter in profile_linters if linter.profile is not None]
        with_skills = [linter for linter in loaded if linter.skills is not None]

        profile_dirs_by_name: dict[str, str] = {}
        for linter in loaded:
            if linter.profile.name in profile_dirs_by_name:
                self.report.error(
                    f"profile name '{linter.profile.name}' is used by both "
                    f"{profile_dirs_by_name[linter.profile.name]} and {linter.raw_directory}"
                )
            else:
                profile_dirs_by_name[linter.profile.name] = linter.raw_directory

        skill_owners: dict[str, str] = {}
        for linter in with_skills:
            for name in linter.skills.names:
                if name in skill_owners and skill_owners[name] != linter.profile.name:
                    self.report.error(
                        f"skill name '{name}' is provided by both profile {skill_owners[name]} "
                        f"and profile {linter.profile.name}"
                    )
                else:
                    skill_owners[name] = linter.profile.name

        total = self.framework_skills.description_chars
        for linter in with_skills:
            total += linter.skills.description_chars
        if total > SKILL_DESCRIPTIONS_WARN_TOTAL_CHARS:
            self.report.warn(
                f"framework and profile skill descriptions total {total} chars "
                f"(warning above {SKILL_DESCRIPTIONS_WARN_TOTAL_CHARS}, the Codex skill-list budget)"
            )


class ProfileLinter:
    """Checks one profile directory against the profile contract and the framework's rules.

    Findings are labelled relative to the profile root; the caller adds the profile prefix.
    """

    def __init__(
        self,
        directory: Path,
        raw_directory: str,
        framework: Framework,
        framework_skill_names: set[str],
        banned_patterns: list[dict],
    ) -> None:
        self.directory = directory
        self.raw_directory = raw_directory
        self.framework = framework
        self.framework_skill_names = framework_skill_names
        self.banned_patterns = banned_patterns
        self.report = Report()
        self.profile: Profile | None = None
        self.skills: SkillTreeChecker | None = None

    @property
    def display_name(self) -> str:
        if self.profile is not None:
            return self.profile.name
        return self.raw_directory

    def rel(self, path: Path) -> str:
        return relative_posix(path, self.profile.root)

    def run(self) -> Report:
        try:
            self.profile = load_profile(self.directory, self.framework.version())
        except (PakemError, OSError, UnicodeDecodeError) as exc:
            self.report.error(str(exc))
            return self.report
        try:
            self.check_version_file()
            self.check_digest()
            self.check_skills()
            self.check_markdown_links()
            self.check_banned_patterns()
        except PakemError as exc:
            self.report.error(str(exc))
        return self.report

    def check_version_file(self) -> None:
        version_file = self.profile.root / "VERSION"
        if not version_file.is_file():
            return
        file_version = read_text(version_file).strip()
        if file_version != self.profile.version:
            self.report.error(
                f"VERSION is {file_version!r} but profile.toml version is {self.profile.version!r}"
            )

    def check_digest(self) -> None:
        label = self.rel(self.profile.digest_path)
        digest = self.profile.digest_text()
        if len(digest) > PROFILE_DIGEST_ERROR_CHARS:
            self.report.error(f"{label} is {len(digest)} chars (limit {PROFILE_DIGEST_ERROR_CHARS})")
        elif len(digest) > PROFILE_DIGEST_WARN_CHARS:
            self.report.warn(f"{label} is {len(digest)} chars (warning above {PROFILE_DIGEST_WARN_CHARS})")

        kernel_path = self.framework.kernel_path
        if not kernel_path.is_file():
            return
        kernel_lines = {line.strip() for line in read_text(kernel_path).splitlines()}
        for number, line in enumerate(digest.splitlines(), start=1):
            stripped = line.strip()
            # Lines without letters or digits (rules, table separators) carry no rule text.
            if not ALPHANUMERIC.search(stripped):
                continue
            if stripped in kernel_lines:
                self.report.warn(
                    f"{label}:{number}: repeats a kernel/KERNEL.md line. Profiles must not restate the kernel"
                )

    def check_skills(self) -> None:
        skills_dir = self.profile.skills_dir
        if skills_dir is None:
            return
        self.skills = SkillTreeChecker(skills_dir, self.profile.root, self.report)
        self.skills.run()
        for name in self.skills.names:
            if name in self.framework_skill_names:
                self.report.error(
                    f"{self.rel(skills_dir / name)}: skill name '{name}' is also a framework skill"
                )
        today = date.today()
        for path in skill_reference_documents(skills_dir):
            check_status_header(path, self.rel(path), self.report, today)

    def profile_files(self) -> list[Path]:
        return repository_files(self.profile.root, EXCLUDED_TOP_LEVEL)

    def check_markdown_links(self) -> None:
        skill_checked_files: set[Path] = set()
        if self.skills is not None:
            skill_checked_files = self.skills.checked_files
        paths = []
        for path in self.profile_files():
            if path.suffix == ".md" and path not in skill_checked_files:
                paths.append(path)
        check_markdown_links(paths, self.profile.root, self.report)

    def check_banned_patterns(self) -> None:
        if not self.banned_patterns:
            return
        paths = []
        for path in self.profile_files():
            if path.suffix in PROFILE_BANNED_SCAN_SUFFIXES:
                paths.append(path)
        scan_banned_patterns(paths, self.profile.root, self.banned_patterns, self.report)


def run_lint(args) -> int:
    framework = Framework(Path(args.root))
    profile_dirs = getattr(args, "profile", None)
    report = Linter(framework, profile_dirs).run()
    print(f"pakem lint {framework.root}")
    for raw in profile_dirs or []:
        print(f"  profile {raw}")
    report.print()
    return report.exit_code
