"""`pakem migrate export|import`: carry a workspace from one device to the next.

Git already carries what projects commit. A bundle carries what is deliberately kept out
of git or tied to a device, so a new device picks up where the old one stopped:

- Pakem's device settings in ~/.config/pakem (never device.toml, which describes the old
  device and its key). Each server keeps its record, but not the projects the old
  device's key may release: the new device's key is allowed by `pakem setup host`.
- The install choices (agents and profiles), so `pakem setup` offers them as answers.
- Each known project's path, git remote, and branch, with its PROJECT_STATE.md and
  JOURNAL.md (untracked by design: they are how a new session continues the work).
- The profile repositories and the documents store, by remote or by content.
- With --agent-sessions, Claude Code's folder for each project (its memory and its
  conversations), so `claude --resume` works there.

Paths are kept relative to the home folder, so a new user name or home path still lands
each project in the same place. Import never overwrites: anything already on the new
device is kept and named. No secret, key, or `.env` file is ever read into a bundle; the
bundle is private (mode 0600) and the owner moves it by hand, then deletes it.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .common import EXIT_DONE, CommandRefused, PakemError, confirm_steps, display_path, home_dir, run_git
from .device_config import config_dir
from .manifest import load_manifest, state_dir

FORMAT = "pakem.workspace-bundle/v1"
# Device settings that travel; device.toml describes the old device and is never copied.
SETTINGS_FILES = ("config.toml", "hosts.toml", "report.toml", "health.toml", "documents.toml", "private-names.txt")
STATE_FILES = ("PROJECT_STATE.md", "JOURNAL.md")
MIGRATED_INSTALL = "migrated-install.json"
NEVER = (".env", "id_ed25519", "id_rsa", "pakem_device_ed25519")


def relative_to_home(path: Path) -> str:
    resolved = path.expanduser().resolve()
    home = home_dir().resolve()
    return f"~/{resolved.relative_to(home)}" if resolved.is_relative_to(home) else str(resolved)


def from_home(text: str) -> Path:
    return home_dir() / text[2:] if text.startswith("~/") else Path(text)


def claude_folder(path: Path) -> Path:
    """Claude Code keeps a project's memory and conversations in a folder named after its path."""
    return home_dir() / ".claude" / "projects" / str(path).replace("/", "-").replace(".", "-")


def repository(path: Path) -> dict | None:
    """A git work tree's root, origin, and branch; None when `path` is not in one."""
    top = run_git(path, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        return None
    root = Path(top.stdout.strip())
    remote = run_git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    branch = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    return {"root": root, "remote": remote, "branch": branch}


def safe_name(name: str) -> bool:
    return not any(part in name for part in NEVER)


# -- export -----------------------------------------------------------------------------------


@dataclass
class Bundle:
    entries: dict[str, bytes] = field(default_factory=dict)
    manifest: dict = field(default_factory=lambda: {"format": FORMAT, "settings": [], "projects": [],
                                                    "profiles": [], "documents": None, "agent_sessions": []})

    def add(self, name: str, data: bytes) -> None:
        self.entries[name] = data

    def add_tree(self, prefix: str, root: Path) -> int:
        count = 0
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(root).parts \
                    and safe_name(path.name):
                self.add(f"{prefix}/{path.relative_to(root).as_posix()}", path.read_bytes())
                count += 1
        return count


def hosts_without_allowances(text: str) -> str:
    """hosts.toml with each `projects = [...]` line dropped: those name what the old device's
    key may release, which the new device's key is not yet allowed."""
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith("projects = "))


def portable(text: str) -> str:
    """Settings name folders under the home folder as quoted absolute paths; written as
    "~/..." here, and under the new home again on import."""
    return text.replace(f'"{home_dir().resolve()}/', '"~/').replace(f'"{home_dir()}/', '"~/')


def localized(text: str) -> str:
    return text.replace('"~/', f'"{home_dir()}/')


def collect(include_sessions: bool) -> tuple[Bundle, list[str]]:
    from .followups import known_projects

    bundle, notes = Bundle(), []
    for name in SETTINGS_FILES:
        path = config_dir() / name
        if path.is_file():
            text = portable(path.read_text(encoding="utf-8"))
            if name == "hosts.toml":
                text = hosts_without_allowances(text)
            bundle.add(f"settings/{name}", text.encode())
            bundle.manifest["settings"].append(name)
    options = load_manifest().get("install_options")
    options = options if isinstance(options, dict) else {}
    profile_dirs = [str(directory) for directory in options.get("profiles") or []]
    # Relative to the home folder, as everything else here: a new home still finds them.
    bundle.manifest["install"] = {"hosts": options.get("hosts"),
                                  "profiles": [relative_to_home(Path(directory)) for directory in profile_dirs]}

    for directory in profile_dirs:
        repo = repository(Path(directory))
        if repo is None or not repo["remote"]:
            notes.append(f"profile {display_path(Path(directory))} has no git remote: copy it by hand")
            continue
        entry = {"path": relative_to_home(repo["root"]), "remote": repo["remote"], "branch": repo["branch"]}
        if entry not in bundle.manifest["profiles"]:
            bundle.manifest["profiles"].append(entry)

    repos, problems = known_projects()
    notes += problems
    for number, path in enumerate(repos):
        repo = repository(path)
        if repo is None:
            continue
        project = {"path": relative_to_home(repo["root"]), "remote": repo["remote"], "branch": repo["branch"],
                   "state": []}
        for name in STATE_FILES:
            state = repo["root"] / name
            if state.is_file():
                bundle.add(f"projects/{number}/{name}", state.read_bytes())
                project["state"].append(name)
        if not repo["remote"]:
            notes.append(f"{display_path(repo['root'])} has no origin remote: its state travels, its code does not")
        project["entry"] = number
        bundle.manifest["projects"].append(project)
        sessions = claude_folder(repo["root"])
        if include_sessions and sessions.is_dir():
            files = bundle.add_tree(f"sessions/{number}", sessions)
            bundle.manifest["agent_sessions"].append({"project": project["path"], "entry": number, "files": files})

    documents = config_dir() / "documents.toml"
    if documents.is_file():
        store = Path(str(tomllib.loads(documents.read_text(encoding="utf-8")).get("root", ""))).expanduser()
        if store.is_dir():
            files = bundle.add_tree("documents", store)
            bundle.manifest["documents"] = {"path": relative_to_home(store), "files": files}
    return bundle, notes


def write_bundle(bundle: Bundle, target: Path) -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, data in [("manifest.json", json.dumps(bundle.manifest, indent=2).encode()),
                           *sorted(bundle.entries.items())]:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, int(time.time())
            archive.addfile(info, io.BytesIO(data))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload.getvalue())


def run_export(args) -> int:
    target = Path(args.to).expanduser() if args.to else home_dir() / f"pakem-workspace-{time.strftime('%Y%m%d')}.tar.gz"
    if target.exists():
        raise CommandRefused(f"{display_path(target)} exists already. Name another file with --to")
    bundle, notes = collect(args.agent_sessions)
    summary = [
        f"settings: {', '.join(bundle.manifest['settings']) or 'none'}",
        f"projects: {len(bundle.manifest['projects'])}, with "
        f"{sum(len(project['state']) for project in bundle.manifest['projects'])} state files",
        f"profile repositories: {len(bundle.manifest['profiles'])}",
        f"documents store: {bundle.manifest['documents']['files'] if bundle.manifest['documents'] else 0} files",
        f"agent sessions: {len(bundle.manifest['agent_sessions'])} projects"
        + ("" if args.agent_sessions else " (add --agent-sessions to include them)"),
    ]
    print(f"pakem migrate export to {display_path(target)}")
    for line in summary + notes:
        print(f"  {line}")
    if args.plan:
        print("Plan only: nothing written.")
        return EXIT_DONE
    write_bundle(bundle, target)
    print(f"Wrote {display_path(target)} (private, mode 0600). It holds your projects' notes and your settings: "
          "move it to the new device yourself, run the installer there (or `pakem migrate import <file>`), "
          "then delete it.")
    return EXIT_DONE


# -- import -----------------------------------------------------------------------------------


def read_bundle(path: Path) -> tuple[dict, dict[str, bytes]]:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            entries = {}
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if not member.isfile() or name.is_absolute() or ".." in name.parts:
                    raise PakemError(f"{display_path(path)} holds an unexpected entry {member.name!r}")
                entries[member.name] = archive.extractfile(member).read()
    except (OSError, tarfile.TarError) as exc:
        raise PakemError(f"cannot read the bundle {display_path(path)}: {exc}") from exc
    manifest = json.loads(entries.pop("manifest.json", b"{}") or b"{}")
    if manifest.get("format") != FORMAT:
        raise PakemError(f"{display_path(path)} is not a Pakem workspace bundle ({FORMAT})")
    return manifest, entries


def write_new(path: Path, data: bytes, kept: list[str], mode: int = 0o600) -> bool:
    if path.exists():
        kept.append(display_path(path))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return True


def clone(entry: dict, done: list[str], kept: list[str], failed: list[str]) -> Path:
    path = from_home(entry["path"])
    if path.exists():
        kept.append(display_path(path))
        return path
    if not entry.get("remote"):
        failed.append(f"{entry['path']}: no remote recorded, so its code must be copied by hand")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--quiet", entry["remote"], str(path)]
    if subprocess.run(command, check=False).returncode != 0:
        failed.append(f"{entry['path']}: cloning {entry['remote']} failed (check your access)")
        return path
    if entry.get("branch"):
        run_git(path, "checkout", "--quiet", entry["branch"], check=False)
    done.append(f"cloned {entry['path']}")
    return path


def exclude(repo: Path, names: list[str]) -> None:
    """Keep restored state files untracked, as they were on the old device."""
    path = repo / ".git" / "info" / "exclude"
    if not path.parent.is_dir():
        return
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [name for name in names if name not in lines]
    if missing:
        path.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")


def plan_import(manifest: dict) -> list[str]:
    return [
        f"settings into {display_path(config_dir())} (each one this device lacks): {', '.join(manifest['settings'])}",
        f"clone {len(manifest['profiles'])} profile repositories and {len(manifest['projects'])} projects "
        "that are not here yet, to the same places under your home folder",
        "put back each project's PROJECT_STATE.md and JOURNAL.md where the project has none",
        "the documents store, if this device has none" if manifest.get("documents") else "no documents store",
        f"Claude Code sessions for {len(manifest['agent_sessions'])} projects" if manifest["agent_sessions"]
        else "no agent sessions",
    ]


def import_bundle(path: Path) -> tuple[list[str], list[str], list[str]]:
    manifest, entries = read_bundle(path)
    done: list[str] = []
    kept: list[str] = []
    failed: list[str] = []
    for name in manifest["settings"]:
        data = localized(entries[f"settings/{name}"].decode()).encode()
        if write_new(config_dir() / name, data, kept):
            done.append(f"restored {name}")
    if manifest.get("install"):
        install = dict(manifest["install"])
        install["profiles"] = [str(from_home(directory)) for directory in install.get("profiles") or []]
        target = state_dir() / MIGRATED_INSTALL
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(install, indent=2) + "\n", encoding="utf-8")
    for entry in manifest["profiles"]:
        clone(entry, done, kept, failed)
    for project in manifest["projects"]:
        repo = clone(project, done, kept, failed)
        restored = []
        for name in project["state"]:
            if repo.is_dir() and write_new(repo / name, entries[f"projects/{project['entry']}/{name}"], kept, 0o644):
                restored.append(name)
        if restored:
            exclude(repo, restored)
            done.append(f"restored {', '.join(restored)} in {project['path']}")
    if manifest.get("documents"):
        store = from_home(manifest["documents"]["path"])
        prefix = "documents/"
        if store.exists():
            kept.append(display_path(store))
        else:
            for name, data in entries.items():
                if name.startswith(prefix):
                    write_new(store / name[len(prefix):], data, kept)
            done.append(f"restored the documents store {manifest['documents']['path']}")
    for session in manifest["agent_sessions"]:
        folder = claude_folder(from_home(session["project"]))
        prefix = f"sessions/{session['entry']}/"
        if folder.exists():
            kept.append(display_path(folder))
            continue
        for name, data in entries.items():
            if name.startswith(prefix):
                write_new(folder / name[len(prefix):], data, kept)
        done.append(f"restored the Claude Code sessions of {session['project']}")
    return done, kept, failed


def run_import(args) -> int:
    path = Path(args.bundle).expanduser()
    manifest, _ = read_bundle(path)
    print(f"pakem migrate import {display_path(path)}")
    for line in plan_import(manifest):
        print(f"  {line}")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem migrate import")
    done, kept, failed = import_bundle(path)
    for line in done:
        print(f"  {line}")
    if kept:
        print(f"Kept as they are here ({len(kept)}): {', '.join(kept[:8])}{' ...' if len(kept) > 8 else ''}")
    for line in failed:
        print(f"NOTE: {line}")
    print("Next: `pakem setup` (it offers the old device's agents and profiles), then set up your servers there "
          f"so this device's key may release. When everything is here, delete {display_path(path)}.")
    return EXIT_DONE


def add_parser(commands) -> None:
    migrate = commands.add_parser("migrate", help="carry a workspace to a new device: export here, import there")
    sub = migrate.add_subparsers(dest="migrate_command", required=True)
    export = sub.add_parser("export", help="write one private bundle of what git does not carry")
    export.add_argument("--to", metavar="FILE", help="the bundle to write (default ~/pakem-workspace-<date>.tar.gz)")
    export.add_argument("--agent-sessions", action="store_true",
                        help="include Claude Code's folder for each project (its memory and conversations)")
    export.add_argument("--plan", action="store_true", help="list what would go in the bundle, write nothing")
    imported = sub.add_parser("import", help="put a bundle's settings, projects, and notes in place, never overwriting")
    imported.add_argument("bundle", help="the file `pakem migrate export` wrote")
    imported.add_argument("--plan", action="store_true", help="show what would be done, change nothing")
    imported.add_argument("--yes", action="store_true", help="do not ask for confirmation")


def run_migrate(args) -> int:
    return run_export(args) if args.migrate_command == "export" else run_import(args)
