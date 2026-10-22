"""`pakem enforce install|uninstall`: host-level guardrails (settings, rules, hooks).

Every change made to a host's JSON configuration is recorded in the manifest so
uninstall removes exactly what pakem added and nothing the owner added later.
"""

from __future__ import annotations

import copy
import difflib
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .common import (
    PakemError,
    Framework,
    display_path,
    expand_user_path,
    home_dir,
    read_text,
    sha256_file,
    utc_now_iso,
    write_text_atomically,
)
from .manifest import BackupSession, load_manifest, save_manifest, shared_data_dir

GUARD_RECORD = "guard"
GUARD_MODE = 0o755
JSON_HOSTS = {
    "claude": ("claude/settings.fragment.json", ".claude/settings.json"),
    "antigravity": ("antigravity/hooks.fragment.json", ".gemini/config/hooks.json"),
}
CODEX_RULES_SOURCE = "codex/pakem.rules"
CODEX_RULES_DESTINATION = ".codex/rules/pakem.rules"
CODEX_PROBE_COMMAND = ["git", "push", "--force", "origin", "main"]
# Fragments name the pakem command this way; hooks need the tool itself, not a copy.
PAKEM_PLACEHOLDER = "{{pakem}}"


WRAPPER_MARKER = "# pakem-managed"


def pakem_command(framework: Framework) -> str:
    """How a host hook runs this checkout's tool.

    The install.sh wrapper on PATH is preferred when it runs this checkout: its path
    survives interpreter upgrades, while sys.executable may name a versioned interpreter.
    Otherwise the interpreter running now is used, since it is known to be 3.11+.
    """
    tool = framework.root.resolve() / "tools" / "pakem"
    wrapper = shutil.which("pakem")
    if wrapper is not None:
        try:
            text = Path(wrapper).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if WRAPPER_MARKER in text.splitlines() and str(tool) in text:
            return shlex.quote(wrapper)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(tool))}"


def render_fragment(value: object, command: str) -> object:
    if isinstance(value, str):
        return value.replace(PAKEM_PLACEHOLDER, command)
    if isinstance(value, list):
        return [render_fragment(item, command) for item in value]
    if isinstance(value, dict):
        return {key: render_fragment(item, command) for key, item in value.items()}
    return value


def merge_fragment(target: dict, fragment: dict, path: tuple = ()) -> list[dict]:
    """Merge fragment into target in place; return the list of changes made."""
    changes: list[dict] = []
    for key, value in fragment.items():
        key_path = [*path, key]
        if key not in target:
            target[key] = copy.deepcopy(value)
            changes.append({"op": "add_key", "path": key_path, "value": value})
            continue
        existing = target[key]
        if isinstance(value, dict):
            if not isinstance(existing, dict):
                raise PakemError(f"cannot merge: '{'.'.join(key_path)}' is not a JSON object")
            changes += merge_fragment(existing, value, tuple(key_path))
        elif isinstance(value, list):
            if not isinstance(existing, list):
                raise PakemError(f"cannot merge: '{'.'.join(key_path)}' is not a JSON array")
            for item in value:
                if item not in existing:
                    existing.append(copy.deepcopy(item))
                    changes.append({"op": "append", "path": key_path, "item": item})
        elif existing != value:
            target[key] = copy.deepcopy(value)
            changes.append({"op": "replace", "path": key_path, "previous": existing, "value": value})
    return changes


def container_at(data: dict, path: list) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def revert_changes(data: dict, changes: list[dict]) -> list[str]:
    """Undo recorded changes in reverse order; return warnings for values changed since."""
    warnings: list[str] = []
    for change in reversed(changes):
        dotted = ".".join(change["path"])
        parent = container_at(data, change["path"][:-1])
        key = change["path"][-1]
        if change["op"] == "append":
            values = container_at(data, change["path"])
            if isinstance(values, list) and change["item"] in values:
                values.remove(change["item"])
            continue
        if not isinstance(parent, dict) or key not in parent:
            continue
        if change["op"] == "add_key":
            if parent[key] == change["value"]:
                del parent[key]
            else:
                warnings.append(f"'{dotted}' was changed after install; left in place")
        elif change["op"] == "replace":
            if parent[key] == change["value"]:
                parent[key] = change["previous"]
            else:
                warnings.append(f"'{dotted}' was changed after install; not restored")
    return warnings


def _fragment_contains(fragment: dict, change: dict) -> bool:
    """Whether the current fragment still asks for a change recorded by an earlier install."""
    value = container_at(fragment, change["path"])
    if change["op"] == "append":
        return isinstance(value, list) and change["item"] in value
    return value is not None and value == change["value"]


def split_stale_changes(changes: list[dict], fragment: dict) -> tuple[list[dict], list[dict]]:
    """Split recorded changes into (still wanted, no longer in the fragment)."""
    kept = [change for change in changes if _fragment_contains(fragment, change)]
    stale = [change for change in changes if not _fragment_contains(fragment, change)]
    return kept, stale


def load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PakemError(
            f"{display_path(path)} is not valid JSON ({exc}); fix it before running pakem enforce"
        ) from exc
    if not isinstance(data, dict):
        raise PakemError(f"{display_path(path)} must contain a JSON object")
    return data


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _print_diff(path: Path, before: str, after: str) -> None:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=display_path(path) if before else "/dev/null",
        tofile=display_path(path),
    )
    text = "".join(diff)
    print(text if text else f"(no changes to {display_path(path)})", end="" if text else "\n")


def guard_destination() -> Path:
    return shared_data_dir() / "guard.py"


def _install_guard(framework: Framework, manifest: dict, host: str, apply: bool) -> None:
    source = framework.enforcement_dir / "guard.py"
    destination = guard_destination()
    record = manifest["enforcement"].get(GUARD_RECORD)
    source_text = read_text(source)
    if destination.exists():
        current_sha = sha256_file(destination)
        managed = record is not None and record.get("sha256") == current_sha
        if not managed and read_text(destination) != source_text:
            raise PakemError(
                f"{display_path(destination)} exists and was not installed by pakem; refusing to overwrite"
            )
    print(f"  guard: {display_path(destination)}")
    if not apply:
        return
    write_text_atomically(destination, source_text, mode=GUARD_MODE)
    users = set(record.get("users", [])) if record else set()
    users.add(host)
    manifest["enforcement"][GUARD_RECORD] = {
        "path": display_path(destination),
        "sha256": sha256_file(destination),
        "users": sorted(users),
    }


def _release_guard(manifest: dict, host: str, apply: bool) -> None:
    record = manifest["enforcement"].get(GUARD_RECORD)
    if record is None:
        return
    users = [user for user in record.get("users", []) if user != host]
    destination = expand_user_path(record["path"])
    if users:
        print(f"  guard kept for: {', '.join(users)}")
        if apply:
            record["users"] = users
        return
    if destination.exists() and sha256_file(destination) != record.get("sha256"):
        print(f"WARNING: {record['path']} was edited after install; left in place")
        if apply:
            del manifest["enforcement"][GUARD_RECORD]
        return
    print(f"  remove guard: {record['path']}")
    if apply:
        if destination.exists():
            destination.unlink()
        del manifest["enforcement"][GUARD_RECORD]


def install_json_host(framework: Framework, host: str, apply: bool) -> int:
    fragment_relative, target_relative = JSON_HOSTS[host]
    raw_fragment = json.loads(read_text(framework.enforcement_dir / fragment_relative))
    fragment = render_fragment(raw_fragment, pakem_command(framework))
    target_path = home_dir() / target_relative
    manifest = load_manifest()
    existed = target_path.exists()
    before_text = target_path.read_text(encoding="utf-8") if existed else ""
    data = load_json_object(target_path)
    previous = manifest["enforcement"].get(host)
    kept_changes, stale_changes = split_stale_changes(previous.get("changes", []) if previous else [], fragment)
    # Entries a newer fragment dropped would otherwise stay in the host settings forever.
    warnings = revert_changes(data, stale_changes)
    changes = merge_fragment(data, fragment)
    after_text = render_json(data)

    print(f"pakem enforce install --host {host} ({'apply' if apply else 'dry-run'})")
    for change in stale_changes:
        detail = change.get("item", change.get("value"))
        print(f"  remove stale: {'.'.join(change['path'])} {json.dumps(detail)}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    if changes or stale_changes:
        _print_diff(target_path, before_text, after_text)
    else:
        print(f"  unchanged: {display_path(target_path)} already contains the fragment")
    _install_guard(framework, manifest, host, apply)
    if not apply:
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    record = previous or {
        "target": display_path(target_path),
        "created_file": not existed,
        "changes": [],
    }
    if changes or stale_changes:
        if existed:
            backup = BackupSession().back_up(target_path, f"enforce-{host}")
            manifest["backups"].append(backup)
        write_text_atomically(target_path, after_text)
        record["changes"] = kept_changes + changes
    record["installed_at"] = utc_now_iso()
    manifest["enforcement"][host] = record
    save_manifest(manifest)
    print("Installed.")
    return 0


def uninstall_json_host(host: str, apply: bool) -> int:
    manifest = load_manifest()
    record = manifest["enforcement"].get(host)
    print(f"pakem enforce uninstall --host {host} ({'apply' if apply else 'dry-run'})")
    if record is None:
        print(f"  nothing recorded for {host}")
        return 0
    target_path = expand_user_path(record["target"])
    before_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    data = load_json_object(target_path)
    warnings = revert_changes(data, record.get("changes", []))
    remove_file = record.get("created_file") and data == {}
    if remove_file:
        print(f"  remove {display_path(target_path)} (created by pakem and now empty)")
    else:
        _print_diff(target_path, before_text, render_json(data))
    for warning in warnings:
        print(f"WARNING: {warning}")
    _release_guard(manifest, host, apply)
    if not apply:
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0

    if target_path.exists():
        manifest["backups"].append(BackupSession().back_up(target_path, f"enforce-uninstall-{host}"))
        if remove_file:
            target_path.unlink()
        else:
            write_text_atomically(target_path, render_json(data))
    del manifest["enforcement"][host]
    save_manifest(manifest)
    print("Uninstalled.")
    return 0


def validate_codex_rules(rules_path: Path) -> None:
    codex = shutil.which("codex")
    if codex is None:
        print("WARNING: codex is not on PATH; rules syntax not verified with `codex execpolicy check`")
        return
    completed = subprocess.run(
        [codex, "execpolicy", "check", "--rules", str(rules_path), *CODEX_PROBE_COMMAND],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PakemError(f"codex execpolicy rejected {rules_path.name}: {completed.stderr.strip()}")
    try:
        decision = json.loads(completed.stdout).get("decision")
    except json.JSONDecodeError as exc:
        raise PakemError(f"unexpected codex execpolicy output: {completed.stdout[:200]!r}") from exc
    if decision != "forbidden":
        raise PakemError(f"rules did not forbid '{' '.join(CODEX_PROBE_COMMAND)}' (decision: {decision})")
    print(f"  verified with codex execpolicy: '{' '.join(CODEX_PROBE_COMMAND)}' -> forbidden")


def install_codex(framework: Framework, apply: bool) -> int:
    source = framework.enforcement_dir / CODEX_RULES_SOURCE
    destination = home_dir() / CODEX_RULES_DESTINATION
    manifest = load_manifest()
    record = manifest["enforcement"].get("codex")
    source_text = read_text(source)
    print(f"pakem enforce install --host codex ({'apply' if apply else 'dry-run'})")
    validate_codex_rules(source)

    before_text = ""
    if destination.exists():
        before_text = read_text(destination)
        managed = record is not None and record.get("sha256") == sha256_file(destination)
        if not managed and before_text != source_text:
            print(f"ERROR: {display_path(destination)} exists and was not installed by pakem")
            return 1
    _print_diff(destination, before_text, source_text)
    if not apply:
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0
    write_text_atomically(destination, source_text)
    manifest["enforcement"]["codex"] = {
        "target": display_path(destination),
        "sha256": sha256_file(destination),
        "installed_at": utc_now_iso(),
    }
    save_manifest(manifest)
    print("Installed.")
    return 0


def uninstall_codex(apply: bool) -> int:
    manifest = load_manifest()
    record = manifest["enforcement"].get("codex")
    print(f"pakem enforce uninstall --host codex ({'apply' if apply else 'dry-run'})")
    if record is None:
        print("  nothing recorded for codex")
        return 0
    destination = expand_user_path(record["target"])
    if destination.exists() and sha256_file(destination) != record.get("sha256"):
        print(f"ERROR: {record['target']} was edited after install; remove it by hand if intended")
        return 1
    print(f"  remove {record['target']}")
    if not apply:
        print("Dry run: nothing removed. Re-run with --apply to remove.")
        return 0
    if destination.exists():
        destination.unlink()
    del manifest["enforcement"]["codex"]
    save_manifest(manifest)
    print("Uninstalled.")
    return 0


def run_enforce(args) -> int:
    framework = Framework(Path(args.root))
    if args.enforce_command == "install":
        if args.host == "codex":
            return install_codex(framework, args.apply)
        return install_json_host(framework, args.host, args.apply)
    if args.host == "codex":
        return uninstall_codex(args.apply)
    return uninstall_json_host(args.host, args.apply)
