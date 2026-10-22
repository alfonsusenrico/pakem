"""`pakem status`: the installed framework against merged `main`, and this project's pins.

It also serves the two session hooks that `pakem enforce install` adds:

- `--hook session-start` prints the brief status into a new session and records which
  installed files that session loaded;
- `--hook prompt` runs on each message without network and, when an install happened
  since the session started, names the kernel and skill files to re-read.

A hook never fails a session: errors are reported in one line and the exit code is 0.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .common import Framework, PakemError, display_path, normalize_newlines, read_text, run_git, write_text_atomically
from .device_config import UPDATE_APPLY, setting
from .manifest import load_manifest, state_dir
from .pinned import PROFILE_KIND, parse_document
from .project import AGENTS_FILE, check_repo
from .upstream import (
    NOT_INSTALLED,
    UNKNOWN,
    UNTRACKED_SOURCE,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    FetchState,
    FrameworkStatus,
    framework_status,
    refresh,
    seconds_since,
)

HOOK_SESSION_START = "session-start"
HOOK_PROMPT = "prompt"
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SESSION_KEEP_SECONDS = 14 * 24 * 3600
HOOK_INPUT_LIMIT = 1_000_000


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 120:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"


def _checked(fetch: FetchState) -> str:
    text = f"checked {_age(seconds_since(fetch.checked_at))}"
    if fetch.error:
        text += f"; last check failed: {fetch.error}"
    return text


def framework_line(status: FrameworkStatus) -> str:
    version = status.installed_version
    if status.state == NOT_INSTALLED:
        return "Pakem is not installed on this device (pakem install)."
    if status.state == UNTRACKED_SOURCE:
        return f"Pakem {version} installed; update check off: {status.notes[0]}."
    if status.state == UP_TO_DATE:
        return f"Pakem {version} is up to date with {status.target.ref} ({_checked(status.fetch)})."
    if status.state == UPDATE_AVAILABLE:
        line = f"Pakem update available: {version} -> {status.target.version} on {status.target.ref}"
        if status.changed:
            line += f" (changes: {', '.join(status.changed)})"
        return line + "."
    reason = "; ".join(status.notes) or "no target known"
    return f"Pakem {version} installed; update state unknown: {reason} ({_checked(status.fetch)})."


def update_advice(status: FrameworkStatus, for_agent: bool) -> str | None:
    if status.state != UPDATE_AVAILABLE:
        return None
    if not for_agent:
        return "Run `pakem update` to install it."
    if setting("update") == UPDATE_APPLY:
        return "The owner allows agents to update: run `pakem update --yes`, then re-read the files it lists."
    return (
        "Tell the owner an update is available; do not install it yourself "
        "(`pakem config set update apply` would allow that)."
    )


def project_root(cwd: Path) -> Path | None:
    completed = run_git(cwd, "rev-parse", "--show-toplevel", check=False)
    if completed.returncode != 0:
        return None
    root = Path(completed.stdout.strip())
    if not (root / AGENTS_FILE).is_file():
        return None
    return root


def project_status(framework: Framework, manifest: dict, cwd: Path) -> dict | None:
    """Profile pins in AGENTS.md against the installed profiles, and the project check counts."""
    repo = project_root(cwd)
    if repo is None:
        return None
    result: dict = {"root": str(repo), "pins": [], "check": None, "problems": []}
    document = parse_document(normalize_newlines(read_text(repo / AGENTS_FILE)))
    installed = {profile["name"]: profile["version"] for profile in manifest.get("profiles") or []}
    for block in document.blocks:
        if block.kind != PROFILE_KIND:
            continue
        name = block.attributes.get("name", "")
        pinned = block.attributes.get("version", "")
        current = installed.get(name)
        result["pins"].append({"profile": name, "pinned": pinned, "installed": current})
        if "profiles" not in manifest:
            continue
        if current is None:
            result["problems"].append(f"AGENTS.md pins profile {name} {pinned}, which is not installed on this device")
        elif current != pinned:
            result["problems"].append(
                f"AGENTS.md pins profile {name} {pinned}; installed is {current}: "
                "run `pakem project sync --apply` on a branch"
            )
    try:
        report = check_repo(framework, repo)
    except PakemError as exc:
        result["problems"].append(f"project check could not run: {exc}")
        return result
    result["check"] = {"errors": len(report.errors), "warnings": len(report.warnings)}
    if report.errors:
        result["problems"].append(f"project check: {len(report.errors)} error(s); run `pakem project check`")
    return result


def collect(framework: Framework, cwd: Path, network: bool, force: bool) -> tuple[FrameworkStatus, dict | None]:
    manifest = load_manifest()
    fetch = refresh(framework.root, force=force, allow_network=network)
    status = framework_status(framework.root, manifest, fetch)
    return status, project_status(framework, manifest, cwd)


def render_lines(status: FrameworkStatus, project: dict | None, brief: bool, for_agent: bool) -> list[str]:
    lines = [framework_line(status)]
    advice = update_advice(status, for_agent)
    if advice:
        lines.append(advice)
    if not brief:
        lines.append(f"Framework checkout: {display_path(status.root)} ({status.follows or 'follows nothing'})")
        if status.state not in (UNTRACKED_SOURCE, UNKNOWN):
            lines += [f"Note: {note}" for note in status.notes]
        if status.state == UP_TO_DATE or status.state == UPDATE_AVAILABLE:
            lines.append(f"Update check: {_checked(status.fetch)}")
        lines.append(f"Updates by agents: {setting('update')}")
    if project is None:
        if not brief:
            lines.append("Project: none here (no git repository with AGENTS.md)")
        return lines
    name = Path(project["root"]).name
    if not brief:
        pins = ", ".join(f"{pin['profile']} {pin['pinned']}" for pin in project["pins"]) or "no profile blocks"
        lines.append(f"Project {name}: pins {pins}")
        if project["check"] is not None:
            check = project["check"]
            lines.append(f"Project check: {check['errors']} error(s), {check['warnings']} warning(s)")
    lines += [f"Project {name}: {problem}" for problem in project["problems"]]
    return lines


def as_json(status: FrameworkStatus, project: dict | None) -> dict:
    checks = [{"name": "framework", "ok": status.state == UP_TO_DATE, "detail": framework_line(status)}]
    if project is not None:
        checks.append({"name": "project", "ok": not project["problems"], "detail": "; ".join(project["problems"])})
    framework = asdict(status)
    framework["root"] = str(status.root)
    return {
        "status": status.state,
        "done": [],
        "owner_actions": [],
        "gaps": [],
        "checks": checks,
        "framework": framework,
        "project": project,
    }


# Session hooks.


def _sessions_dir() -> Path:
    return state_dir() / "sessions"


def _host_entries(manifest: dict, host: str) -> dict[str, dict]:
    entries = {}
    for entry in manifest.get("entries", []):
        if host in str(entry.get("host", "")).split(","):
            entries[entry["path"]] = {"kind": entry.get("kind"), "sha256": entry.get("sha256")}
    return entries


def _snapshot(manifest: dict, host: str) -> dict:
    return {
        "version": manifest.get("framework_version"),
        "entries": _host_entries(manifest, host),
    }


def _read_hook_input() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = sys.stdin.read(HOOK_INPUT_LIMIT)
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _session_path(hook_input: dict) -> Path | None:
    session_id = hook_input.get("session_id")
    if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
        return None
    return _sessions_dir() / f"{session_id}.json"


def _prune_sessions() -> None:
    directory = _sessions_dir()
    if not directory.is_dir():
        return
    cutoff = time.time() - SESSION_KEEP_SECONDS
    for path in directory.glob("*.json"):
        if path.stat().st_mtime < cutoff:
            path.unlink()


def _reread_list(before: dict, after: dict) -> tuple[list[str], list[str]]:
    reread: list[str] = []
    removed: list[str] = []
    for path, entry in sorted(after["entries"].items()):
        previous = before["entries"].get(path)
        if previous is not None and previous.get("sha256") == entry.get("sha256"):
            continue
        reread.append(f"{path}/SKILL.md" if entry.get("kind") == "skill" else path)
    for path in sorted(set(before["entries"]) - set(after["entries"])):
        removed.append(path)
    return reread, removed


def _hook_session_start(framework: Framework, host: str, hook_input: dict) -> None:
    manifest = load_manifest()
    session = _session_path(hook_input)
    if session is not None:
        _prune_sessions()
        write_text_atomically(session, json.dumps(_snapshot(manifest, host)) + "\n")
    cwd = Path(hook_input["cwd"]) if isinstance(hook_input.get("cwd"), str) else Path.cwd()
    status, project = collect(framework, cwd, network=True, force=False)
    for line in render_lines(status, project, brief=True, for_agent=True):
        print(line)


def _hook_prompt(host: str, hook_input: dict) -> None:
    session = _session_path(hook_input)
    if session is None:
        return
    manifest = load_manifest()
    current = _snapshot(manifest, host)
    if not session.exists():
        write_text_atomically(session, json.dumps(current) + "\n")
        return
    before = json.loads(session.read_text(encoding="utf-8"))
    # installed_at has one-second resolution, so the entries themselves are compared.
    if before.get("entries") == current["entries"]:
        return
    write_text_atomically(session, json.dumps(current) + "\n")
    reread, removed = _reread_list(before, current)
    if not reread and not removed:
        return
    print(f"Pakem was installed during this session ({before.get('version')} -> {current['version']}).")
    if reread:
        print(f"Re-read before continuing: {', '.join(reread)}")
    if removed:
        print(f"No longer installed, stop using: {', '.join(removed)}")


def run_hook(framework: Framework, hook: str, host: str) -> int:
    try:
        hook_input = _read_hook_input()
        if hook == HOOK_SESSION_START:
            _hook_session_start(framework, host, hook_input)
        else:
            _hook_prompt(host, hook_input)
    except Exception as exc:  # noqa: BLE001 - a hook must never break the agent session
        if hook == HOOK_SESSION_START:
            print(f"Pakem status could not run ({type(exc).__name__}: {exc}); run `pakem status`.")
    return 0


def run_status(args) -> int:
    framework = Framework(Path(args.root))
    if args.hook:
        return run_hook(framework, args.hook, args.host)
    status, project = collect(framework, Path.cwd(), network=not args.offline, force=args.refresh)
    if args.json:
        print(json.dumps(as_json(status, project), indent=2))
        return 0
    for line in render_lines(status, project, brief=args.brief, for_agent=False):
        print(line)
    return 0
