"""`pakem setup`: one guided pass that makes a device ready, or reconfigures it.

The one-line installer ends here, and running it again (or `pakem setup`) reconfigures:
every question offers the device's current answer, and only what changes is done again.

    0. Moving from another device: import its bundle (`pakem migrate export` there) first,
       so every later question starts from the old device's answers.
    1. Updates: follow Pakem's releases, or `main`.
    2. Agents: the coding agents found on this device.
    3. Profiles: git repositories or folders, cloned once; or a new one from the example.
    4. Device: the forges and containers this device works with, and how updates arrive.
    5. Install the kernel and skills, with those agents and profiles.
    6. Tools, logins, and the device key (what `pakem setup device` prepares).
    7. Guardrails: each agent's hooks and the session-start notice (`pakem enforce`).
    8. Servers: set up or update each server this device releases to, or add one; each
       runs `pakem setup host` in the owner's terminal with the owner's own login.
    9. Summary: `pakem setup check`, and what only the owner can still do.

It asks only what it cannot find out, and without a terminal it asks nothing: an agent or
a script passes flags and `--yes`. It installs nothing without the owner's go (one
confirmation, or `--yes`), never prints a secret, and never copies a device key. Signing
in to a forge and the key's passphrase happen in the owner's own terminal.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .common import CommandRefused, Framework, PakemError, display_path, expand_user_path, run_git
from .device_config import SETTINGS, load_config, save_setting
from .host import load_hosts
from .hosts import load_targets
from .manifest import load_manifest, state_dir
from .migrate import MIGRATED_INSTALL, plan_import, read_bundle
from .profiles import PROFILE_NAME_PATTERN, load_profile
from .requirements import LOGINS, assess, device_needs, prepare
from .source import has_uncommitted_changes
from .upstream import FOLLOW_MAIN, FOLLOW_RELEASES, follow_mode, newest_release

DEFAULT_PROFILES_DIR = "~/.local/share/pakem/profiles"
EXAMPLE_PROFILE = Path("profiles") / "example"
FORGE_COMMANDS = {"github": "gh", "gitlab": "glab"}


class Prompter:
    """Questions on a terminal; without one, every answer is its default."""

    def __init__(self, interactive: bool, read: Callable[[str], str] = input, write: Callable[[str], None] = print):
        self.interactive = interactive
        self.read = read
        self.write = write

    def choose(self, question: str, choices: tuple[str, ...], default: str) -> str:
        if not self.interactive:
            return default
        while True:
            answer = self.read(f"{question} [{'/'.join(choices)}] (default {default}): ").strip() or default
            if answer in choices:
                return answer
            self.write(f"  please answer one of: {', '.join(choices)}")

    def text(self, question: str, default: str) -> str:
        if not self.interactive:
            return default
        shown = f" (default {default})" if default else ""
        return self.read(f"{question}{shown}: ").strip() or default

    def confirm(self, question: str, default: bool) -> bool:
        if not self.interactive:
            return default
        answer = self.read(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer in ("y", "yes")


@dataclass
class Plan:
    follow: str | None
    hosts: list[str]
    profiles: list[Path]
    settings: dict[str, str]
    guardrails: bool
    tools: bool
    # Recorded host names or SSH targets to set up; None asks `pakem setup host` for a new one.
    servers: list[str | None] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def tool_command(framework: Framework, *arguments: str) -> list[str]:
    return [sys.executable, str(framework.root / "tools" / "pakem"), "--root", str(framework.root), *arguments]


def run_step(command: list[str]) -> int:
    """A step as its own pakem command, in the owner's terminal, so its output and any
    question it asks (a passphrase, a login) reach the owner directly."""
    return subprocess.run(command, check=False).returncode


# -- the questions ----------------------------------------------------------------------------


def ask_follow(framework: Framework, prompt: Prompter, flag: str | None) -> str | None:
    current = follow_mode(framework.root)
    if current is None:
        prompt.write(f"Updates: the checkout {display_path(framework.root)} follows its own branch, kept as it is.")
        return None
    return flag or prompt.choose("Updates follow Pakem's releases or its main branch", (FOLLOW_RELEASES, FOLLOW_MAIN),
                                 current)


def install_options(manifest: dict) -> dict:
    """This device's install choices, or, on a device not installed yet, an imported bundle's."""
    options = manifest.get("install_options")
    if isinstance(options, dict):
        return options
    migrated = state_dir() / MIGRATED_INSTALL
    try:
        data = json.loads(migrated.read_text(encoding="utf-8")) if migrated.is_file() else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def installed_hosts(manifest: dict) -> list[str]:
    options = install_options(manifest)
    if options.get("hosts"):
        return [host for host in str(options["hosts"]).split(",") if host]
    return sorted({host for entry in manifest.get("entries", []) for host in str(entry.get("host", "")).split(",")
                   if host and host in ("claude", "codex", "antigravity")})


def ask_hosts(framework: Framework, prompt: Prompter, flag: str | None, manifest: dict) -> list[str]:
    targets = load_targets(framework.targets_path)
    if flag:
        chosen = [host.strip() for host in flag.split(",") if host.strip()]
    else:
        default = installed_hosts(manifest) or [host for host, target in targets.items() if target.present()]
        default = default or list(targets)
        found = ", ".join(f"{host} ({targets[host].label})" for host in default)
        prompt.write(f"Agents: {found}")
        answer = prompt.text("Agents to set up, comma-separated", ",".join(default))
        chosen = [host.strip() for host in answer.split(",") if host.strip()]
    unknown = [host for host in chosen if host not in targets]
    if unknown or not chosen:
        raise PakemError(f"choose agents from: {', '.join(targets)}")
    return chosen


def is_git_url(entry: str) -> bool:
    return "://" in entry or entry.startswith("git@") or entry.endswith(".git")


def profile_dirs(root: Path) -> list[Path]:
    """The profiles a folder holds: itself, or each subfolder with a profile.toml."""
    if (root / "profile.toml").is_file():
        return [root]
    return sorted(path for path in root.iterdir() if (path / "profile.toml").is_file()) if root.is_dir() else []


def fetch_profiles(entry: str, profiles_dir: Path, prompt: Prompter) -> list[Path]:
    """A folder as it is, or a git repository cloned once under `profiles_dir` and updated after."""
    if not is_git_url(entry):
        folder = expand_user_path(entry)
        if not folder.is_dir():
            raise PakemError(f"no profile folder {entry}")
        return profile_dirs(folder)
    name = entry.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    target = profiles_dir / name
    if (target / ".git").exists():
        prompt.write(f"  updating {display_path(target)}")
        run_git(target, "pull", "--quiet", "--ff-only", check=False)
    else:
        prompt.write(f"  cloning {entry} into {display_path(target)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run(["git", "clone", "--quiet", entry, str(target)], check=False).returncode != 0:
            raise PakemError(f"could not clone {entry}. Check the URL and your access to it")
    return profile_dirs(target)


def start_profile(framework: Framework, prompt: Prompter, profiles_dir: Path) -> Path | None:
    if not prompt.confirm("Start your own profile from the example?", False):
        return None
    name = prompt.text("Its name (lowercase letters, digits, hyphens)", "mine")
    if not PROFILE_NAME_PATTERN.match(name):
        raise PakemError(f"invalid profile name {name!r}")
    target = expand_user_path(prompt.text("Its folder", str(profiles_dir / name)))
    if target.exists():
        raise PakemError(f"{display_path(target)} exists already. Choose another folder")
    shutil.copytree(framework.root / EXAMPLE_PROFILE, target)
    for file_name in ("profile.toml", "digest.md"):
        path = target / file_name
        path.write_text(path.read_text(encoding="utf-8").replace('"example"', f'"{name}"')
                        .replace("`example`", f"`{name}`"), encoding="utf-8")
    prompt.write(f"  created {display_path(target)} from the example. Edit its rules (docs/profiles.md), and keep it "
                 "in a repository of your own")
    return target


def ask_profiles(framework: Framework, prompt: Prompter, flags: list[str] | None, profiles_dir: Path,
                 manifest: dict) -> list[Path]:
    options = install_options(manifest)
    current = [Path(path) for path in options.get("profiles") or [] if Path(path).is_dir()]
    if flags is not None:
        entries = flags
    else:
        if current:
            prompt.write("Profiles installed: " + ", ".join(display_path(path) for path in current))
        keep = prompt.confirm("Keep these profiles?", True) if current else True
        chosen = list(current) if keep else []
        more = prompt.text("Profiles to add: git URLs or folders, comma-separated (empty for none)", "")
        entries = [str(path) for path in chosen] + [part.strip() for part in more.split(",") if part.strip()]
    profiles: list[Path] = []
    for entry in entries:
        found = fetch_profiles(entry, profiles_dir, prompt)
        if not found:
            raise PakemError(f"{entry} holds no profile (no profile.toml in it or its folders)")
        profiles += [path for path in found if path not in profiles]
    if flags is None:
        started = start_profile(framework, prompt, profiles_dir)
        if started is not None:
            profiles.append(started)
    for path in profiles:
        load_profile(path, framework.version())
    return profiles


def detected_forges() -> str:
    """The forges this device already signs in to, or GitHub."""
    signed_in = [forge for forge, command in FORGE_COMMANDS.items() if shutil.which(command) and subprocess.run(
        [command, "auth", "status"], capture_output=True, check=False).returncode == 0]
    return "both" if len(signed_in) == 2 else (signed_in[0] if signed_in else "github")


def ask_settings(prompt: Prompter, flags: dict[str, str | None]) -> dict[str, str]:
    current = load_config()
    questions = {
        "forges": "Forges this device works with",
        "containers": "Does this device run containers (Docker)",
        "update": "Updates: notify (tell you), apply (agents may update), or off (no automatic check)",
    }
    answers = {}
    for key, question in questions.items():
        allowed, default = SETTINGS[key]
        default = current.get(key) or (detected_forges() if key == "forges" else default)
        answers[key] = flags.get(key) or prompt.choose(question, allowed, default)
    return answers


def ask_servers(prompt: Prompter, flags: list[str] | None) -> list[str | None]:
    """Servers to set up from this device: the flags, or on a terminal each recorded one
    and any new ones. Setting one up signs in with the owner's own login (ADR 0011)."""
    if flags is not None:
        return list(flags)
    if not prompt.interactive:
        return []
    recorded = load_hosts()
    if not prompt.confirm("Do you release to servers from this device?", bool(recorded)):
        return []
    chosen: list[str | None] = []
    for name, record in recorded.items():
        if prompt.confirm(f"Set up {name} ({record.target}): update its release engine and let this device's key "
                          "release its projects?", True):
            chosen.append(name)
    while prompt.confirm("Add a new server (pakem setup host asks for its details)?", not recorded and not chosen):
        chosen.append(None)
    return chosen


def import_first(framework: Framework, args, prompt: Prompter, run: Callable[[list[str]], int]) -> None:
    """Step 0: a bundle from the old device, imported before any question, so the answers
    it brings (agents, profiles, settings, servers) are the defaults that follow."""
    bundle = args.import_bundle
    if bundle is None and prompt.interactive and not load_manifest().get("framework_version"):
        bundle = prompt.text("Moving from another device? The bundle from `pakem migrate export` there "
                             "(empty to skip)", "") or None
    if bundle is None:
        return
    manifest, _ = read_bundle(expand_user_path(bundle))
    prompt.write("Importing the old device's workspace:")
    for line in plan_import(manifest):
        prompt.write(f"  {line}")
    if not args.yes and not prompt.confirm("Import it now (nothing here is overwritten)?", True):
        return
    if run(tool_command(framework, "migrate", "import", str(expand_user_path(bundle)), "--yes")) != 0:
        raise PakemError("importing the bundle failed. See the lines above")


def build_plan(framework: Framework, args, prompt: Prompter) -> Plan:
    manifest = load_manifest()
    if manifest.get("framework_version"):
        prompt.write(f"Pakem {manifest['framework_version']} is set up here. Reconfiguring: Enter keeps each answer.")
    profiles_dir = expand_user_path(args.profiles_dir or DEFAULT_PROFILES_DIR)
    follow = ask_follow(framework, prompt, args.follow)
    hosts = ask_hosts(framework, prompt, args.hosts, manifest)
    profiles = ask_profiles(framework, prompt, args.profile, profiles_dir, manifest)
    settings = ask_settings(prompt, {"forges": args.forges, "containers": args.containers, "update": args.update})
    guardrails = not args.no_guardrails and prompt.confirm("Turn on each agent's guardrails and session notice?", True)
    servers = ask_servers(prompt, args.server)
    return Plan(follow, hosts, profiles, settings, guardrails, tools=not args.no_tools, servers=servers)


# -- doing it ---------------------------------------------------------------------------------


def switch_follow(framework: Framework, follow: str) -> str | None:
    """Move the checkout to `main` or to the newest release; refused over local changes."""
    if follow_mode(framework.root) == follow:
        return None
    if has_uncommitted_changes(framework.root):
        raise CommandRefused(f"{display_path(framework.root)} has uncommitted changes. Commit or stash them first")
    run_git(framework.root, "fetch", "--quiet", "--tags", "origin")
    if follow == FOLLOW_MAIN:
        run_git(framework.root, "checkout", "--quiet", "-B", "main", "origin/main")
        return "updates follow main"
    tag = newest_release(framework.root)
    if tag is None:
        raise PakemError("no release found on origin/main to follow")
    run_git(framework.root, "checkout", "--quiet", "--detach", tag)
    return f"updates follow releases (now {tag})"


def describe(plan: Plan) -> list[str]:
    lines = []
    if plan.follow:
        lines.append(f"updates follow {plan.follow}")
    lines.append(f"agents: {', '.join(plan.hosts)}")
    lines.append("profiles: " + (", ".join(display_path(path) for path in plan.profiles) or "none"))
    lines.append("device: " + ", ".join(f"{key} {value}" for key, value in plan.settings.items()))
    lines.append("install the kernel and skills for those agents, with those profiles")
    if plan.tools:
        lines.append("prepare the tools, forge logins, and device key this device needs")
    if plan.guardrails:
        lines.append("turn on the guardrails and session notice of each agent")
    for server in plan.servers:
        lines.append(f"set up the server {server} (your own login)" if server else
                     "add a new server: `pakem setup host` asks for its details (your own login)")
    return lines


def sign_in(prompt: Prompter, forges: str) -> list[str]:
    """Offer each missing forge login in the owner's terminal; never reads or prints a token."""
    done = []
    for forge, command in FORGE_COMMANDS.items():
        if forges not in (forge, "both") or shutil.which(command) is None:
            continue
        if subprocess.run([command, "auth", "status"], capture_output=True, check=False).returncode == 0:
            continue
        label = LOGINS[f"{forge}-login"][1]
        if prompt.interactive and prompt.confirm(f"Sign in to {label} now (`{command} auth login`)?", True):
            if subprocess.run([command, "auth", "login"], check=False).returncode == 0:
                done.append(f"signed in to {label}")
    return done


def run_guided(args, prompt: Prompter | None = None, run: Callable[[list[str]], int] = run_step) -> int:
    framework = Framework(Path(args.root))
    interactive = sys.stdin.isatty()
    if not interactive and not args.yes:
        raise PakemError("without a terminal, pass --yes and the answers as flags (see `pakem setup --help`)")
    # --yes takes every default and the flags as the answers, and asks nothing.
    prompt = prompt or Prompter(interactive and not args.yes)
    prompt.write(f"pakem setup: Pakem {framework.version()} in {display_path(framework.root)}")
    import_first(framework, args, prompt, run)
    plan = build_plan(framework, args, prompt)
    prompt.write("\nThe plan:")
    for line in describe(plan):
        prompt.write(f"  {line}")
    if not args.yes and not prompt.confirm("Go ahead?", True):
        raise CommandRefused("pakem setup cancelled. Nothing changed")

    done: list[str] = []
    if plan.follow:
        switched = switch_follow(framework, plan.follow)
        if switched:
            done.append(switched)
    for key, value in plan.settings.items():
        save_setting(key, value)
    install = ["install", "--apply", "--hosts", ",".join(plan.hosts)]
    for path in plan.profiles:
        install += ["--profile", str(path)]
    if run(tool_command(framework, *install)) != 0:
        raise PakemError("installing the kernel and skills failed. See the lines above")
    done.append("installed the kernel and skills")
    if plan.tools:
        needs = tuple(need for need in device_needs() if need != "framework")
        options = argparse.Namespace(plan=False, yes=True, json=False)
        prepare(framework, needs, options, "pakem setup", announce=False)
        done += sign_in(prompt, plan.settings["forges"])
    if plan.guardrails:
        for host in plan.hosts:
            if run(tool_command(framework, "enforce", "install", "--host", host, "--apply")) != 0:
                raise PakemError(f"turning on the {host} guardrails failed. See the lines above")
        done.append("turned on the guardrails")
    for server in plan.servers:
        # Each server is its own `pakem setup host` in the owner's terminal: one failing does
        # not undo the device, and the summary names how to run it again.
        # The owner already accepted the plan, so `setup host` does not ask a second time.
        code = run(tool_command(framework, "setup", "host", *([server] if server else []), "--yes"))
        if code == 0:
            done.append(f"set up {server or 'the new server'}")
        else:
            plan.notes.append(f"The server {server or '(new)'} is not set up (exit {code}). Run "
                              f"`pakem setup host{' ' + server if server else ''}` again in your terminal.")

    assessment = assess(framework, device_needs(), interactive)
    prompt.write("\nThis device:")
    for check in assessment.checks:
        prompt.write(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    for action in assessment.owner_actions:
        prompt.write(action)
    for note in plan.notes:
        prompt.write(note)
    prompt.write("\nDone: " + ", ".join(done) + "." if done else "\nNothing changed.")
    prompt.write("Run the installer again, or `pakem setup`, to change any answer. Next: open your coding agent in a "
                 "project and say \"Set up this project with Pakem.\"")
    return assessment.outcome()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--import", dest="import_bundle", metavar="FILE",
                        help="first import the bundle `pakem migrate export` wrote on your old device")
    parser.add_argument("--yes", action="store_true",
                        help="take every default and the flags as the answers, and apply the plan without asking")
    parser.add_argument("--follow", choices=(FOLLOW_RELEASES, FOLLOW_MAIN), help="what updates follow")
    parser.add_argument("--hosts", help="agents to set up, comma-separated ids (default: the ones found here)")
    parser.add_argument("--profile", action="append", metavar="DIR_OR_GIT_URL",
                        help="a profile folder or a git repository of profiles (repeatable). Default: the installed ones")
    parser.add_argument("--profiles-dir", metavar="DIR", help=f"where profile repositories are cloned ({DEFAULT_PROFILES_DIR})")
    parser.add_argument("--forges", choices=SETTINGS["forges"][0], help="forges this device works with")
    parser.add_argument("--containers", choices=SETTINGS["containers"][0], help="whether this device runs containers")
    parser.add_argument("--update", choices=SETTINGS["update"][0], help="how updates arrive")
    parser.add_argument("--no-guardrails", action="store_true", help="leave the agents' hooks off")
    parser.add_argument("--no-tools", action="store_true", help="install nothing beyond Pakem itself (no tools or logins)")
    parser.add_argument("--server", action="append", metavar="NAME_OR_TARGET",
                        help="a server to set up from this device with `pakem setup host` (repeatable; your own "
                             "login, so it needs a terminal). Default on a terminal: asked")
