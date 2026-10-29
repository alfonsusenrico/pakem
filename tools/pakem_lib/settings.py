"""`pakem secret`, `pakem placement`, and `pakem settings status`: a service's settings on
its host (ADR 0015, area 4; direct-delivery hosts only).

The device signs a `pakem.settings-request/v1` with its device key, as it signs a release
request, and sends it over the same forced-command SSH connection; the value follows the
signed header on standard input and only its digest is signed. The host checks the
signature, the project, freshness, and the digest, and writes as the deploy user.

Names must be declared in the project's deploy/settings.toml with the matching kind, so
nothing undeclared is ever sent. Secret values are read from hidden terminal input, a
desktop dialog, or a file, kept in memory, and never printed, logged, or put in arguments.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    EXIT_DONE,
    EXIT_OWNER_ACTION,
    CheckFailed,
    CommandRefused,
    Framework,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
)
from .host import DEPLOY_USER, DeviceLink, HostRecord, load_hosts, hosts_path
from .project_init import deploy_contracts
from .release import (
    NAMESPACE,
    check_delivery,
    github_repository,
    host_for,
    require_production,
    signed_header,
    utc_now,
)
from .requirements import device_label, ensure
from .service_project import PRODUCTION, STAGING, Project, read_project

SETTINGS_SCHEMA = "pakem.settings-request/v1"
EMPTY_DIGEST = f"sha256:{hashlib.sha256(b'').hexdigest()}"
NEEDS = ("ssh", "device-key", "device-key-unlocked")
GITHUB_NEEDS = ("gh", "github-login")
# The contracts this pakem ships with: the device checks a manifest as its own host would.
TOOL_ROOT = Path(__file__).resolve().parents[2]
# How a receiver from before settings requests answers the `settings` command.
OLD_RECEIVER = "expected `release`, `history <project>`, or `status <project>`"
# A dialog nobody answers is closed after this long, and its secret skipped.
DIALOG_TIMEOUT = 600
# AppleScript's error number when the owner presses Cancel.
APPLESCRIPT_CANCELLED = "-128"
ZENITY_CANCELLED, ZENITY_TIMEOUT = 1, 5
KDIALOG_CANCELLED = 1

PLACEMENT, SECRET, HOST_GENERATED = "placement", "secret", "host_generated"
STORED, GENERATED, REGISTERED, SKIPPED, CANCELLED = "stored", "generated", "registered", "skipped", "cancelled"
VALUE, UNAVAILABLE = "value", "unavailable"
DIALOG, TERMINAL = "dialog", "terminal"


# -- the project's manifest ----------------------------------------------------------------


@dataclass
class Manifest:
    placement: list[str]
    secrets: list[str]
    host_generated: list[str]
    required: list[str]
    descriptions: dict[str, str]
    generate: dict[str, str]

    def kind(self, name: str) -> str | None:
        if name in self.placement:
            return PLACEMENT
        if name in self.secrets:
            return SECRET
        if name in self.host_generated:
            return HOST_GENERATED
        return None

    @property
    def host_names(self) -> list[str]:
        """Every name whose value lives on the host, in manifest order."""
        return [*self.placement, *self.secrets, *self.host_generated]

    def undescribed(self) -> list[str]:
        return [name for name in [*self.placement, *self.secrets] if name not in self.descriptions]


def read_manifest(project: Project) -> Manifest:
    path = project.root / "deploy" / "settings.toml"
    if not path.is_file():
        raise PakemError(f"{display_path(project.root)} has no deploy/settings.toml naming its settings")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"deploy/settings.toml is not valid TOML: {exc}") from exc
    problems = deploy_contracts(Framework(TOOL_ROOT)).validate_settings_manifest(data)
    if problems:
        raise PakemError("; ".join(problems))
    return Manifest(
        list(data.get("placement", [])), list(data.get("secrets", [])), list(data.get("host_generated", [])),
        list(data.get("required", [])), dict(data.get("descriptions", {})), dict(data.get("generate", {})),
    )


def refuse_unless(manifest: Manifest, name: str, wanted: str) -> None:
    """Only names the manifest declares with this kind are ever sent (exit 6 otherwise)."""
    kind = manifest.kind(name)
    if kind == wanted:
        return
    if kind is None:
        raise CommandRefused(f"{name} is not declared in deploy/settings.toml. Declare it first")
    if kind == HOST_GENERATED:
        raise CommandRefused(f"{name} is host_generated in deploy/settings.toml: the engine creates it at deploy")
    if kind == PLACEMENT:
        raise CommandRefused(f"{name} is placement in deploy/settings.toml. Set it with `pakem placement set`")
    raise CommandRefused(f"{name} is a secret in deploy/settings.toml. Set it with `pakem secret set {name}`")


def refuse_kind_mismatch(manifest: Manifest, name: str, kind: str) -> None:
    contracts = deploy_contracts(Framework(TOOL_ROOT))
    if not contracts.generate_kind_ok(kind):
        raise PakemError(f"--generate takes token:<bytes> (16 to 1024), deploy-key:read, or deploy-key:write, not {kind!r}")
    declared = manifest.generate.get(name)
    if declared is not None and declared != kind:
        raise CommandRefused(f"deploy/settings.toml generates {name} as {declared}, not {kind}")


# -- where the settings go -----------------------------------------------------------------


def environment_of(args, project: Project) -> str:
    environment = args.environment or (STAGING if project.has_staging else PRODUCTION)
    if environment not in project.environments:
        raise CommandRefused(f"{project.name} declares no [environments.{environment}] in deploy/release.toml")
    return environment


def settings_host(project: Project) -> HostRecord:
    """The host that releases the project: the one this device may release it on. A host
    that only lists the project's directory still needs the owner's `pakem setup host`."""
    if any(project.name in record.projects for record in load_hosts().values()):
        return host_for(project)
    waiting = []
    for record in load_hosts().values():
        directories = {Path(directory).expanduser().resolve() for directory in record.project_dirs}
        if project.root in directories:
            waiting.append(record)
    if len(waiting) > 1:
        names = ", ".join(record.name for record in waiting)
        raise PakemError(f"{project.name} is recorded for several hosts ({names}) in {display_path(hosts_path())}")
    if waiting:
        raise OwnerActionRequired(
            f"OWNER ACTION REQUIRED: run `pakem setup host {waiting[0].name}` in your terminal, so this device may "
            f"release {project.name} there and write its settings"
        )
    return host_for(project)


@dataclass
class Target:
    project: Project
    record: HostRecord
    environment: str

    @property
    def label(self) -> str:
        return f"{self.project.name} {self.environment} on {self.record.name}"

    def send(self, action: str, names: list[str], value: bytes = b"", kind: str | None = None,
             with_values: bool = False) -> dict:
        """Sign one settings request and send it with its value; returns the host's reply."""
        request = {
            "schema": SETTINGS_SCHEMA, "action": action, "project": self.project.name,
            "environment": self.environment, "names": names, "kind": kind, "with_values": with_values,
            "created": utc_now(), "device": device_label(),
            "digest": f"sha256:{hashlib.sha256(value).hexdigest()}",
        }
        with tempfile.TemporaryDirectory(prefix="pakem-settings-") as directory:
            header = signed_header(Path(directory), request)
        try:
            return DeviceLink(self.record).exchange("settings", header + value)
        except CommandRefused as error:
            if OLD_RECEIVER in str(error):
                raise OwnerActionRequired(
                    f"OWNER ACTION REQUIRED: run `pakem setup host {self.record.name}` in your terminal once, so "
                    f"{self.record.name} has the release receiver that accepts settings requests"
                ) from None
            raise

    def states(self, manifest: Manifest, with_values: bool = False) -> dict[str, dict]:
        """Each declared host name with `set`, and a secret's `age_seconds` (or a placement
        `value` when asked); the host never returns secret values."""
        reply = self.send("list", manifest.host_names, with_values=with_values)
        states = {}
        for name in manifest.host_names:
            table = "placement" if manifest.kind(name) == PLACEMENT else "secrets"
            states[name] = dict(reply.get(table, {}).get(name) or {"set": False})
        return states


def prepare(args) -> tuple[Project, Manifest, str]:
    project = read_project(Path(args.project_dir))
    check_delivery(project)
    return project, read_manifest(project), environment_of(args, project)


def connect(args, project: Project, environment: str, needs: tuple[str, ...], command: str) -> Target:
    record = settings_host(project)
    ensure(Framework(Path(args.root)), needs, args, command)
    return Target(project, record, environment)


# -- reading a secret value ----------------------------------------------------------------


@dataclass
class Answer:
    outcome: str
    # Never shown: a repr or an error message must not carry the value.
    value: bytes = field(default=b"", repr=False)


def dialog_program() -> str | None:
    """The desktop's hidden-input dialog program, when this device shows a desktop."""
    if sys.platform == "darwin":
        return "osascript" if shutil.which("osascript") else None
    if sys.platform.startswith("linux") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return next((program for program in ("zenity", "kdialog") if shutil.which(program)), None)
    return None


def dialog_command(program: str, name: str, text: str) -> list[str]:
    """One hidden-input dialog titled with the name. The name and description are arguments
    of the dialog program, never part of a script."""
    if program == "osascript":
        script = [
            "on run argv",
            'set answer to display dialog (item 2 of argv) with title (item 1 of argv) default answer "" '
            'with hidden answer buttons {"Cancel", "Store"} default button "Store" cancel button "Cancel"',
            "return text returned of answer",
            "end run",
        ]
        return ["osascript", *[part for line in script for part in ("-e", line)], name, text]
    if program == "zenity":
        # zenity's --password dialog shows no text, so its entry dialog with hidden text
        # carries the description.
        return ["zenity", "--entry", "--hide-text", "--title", name, "--text", text]
    return ["kdialog", "--title", name, "--password", text]


def ask_dialog(command: list[str]) -> Answer:
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=DIALOG_TIMEOUT)
    except subprocess.TimeoutExpired:
        return Answer(SKIPPED)
    except OSError:
        return Answer(UNAVAILABLE)
    program = Path(command[0]).name
    if completed.returncode == 0:
        value = completed.stdout[:-1] if completed.stdout.endswith(b"\n") else completed.stdout
        return Answer(VALUE, value) if value else Answer(SKIPPED)
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if program == "osascript" and APPLESCRIPT_CANCELLED in stderr:
        return Answer(CANCELLED)
    if program == "zenity" and completed.returncode == ZENITY_CANCELLED:
        return Answer(CANCELLED)
    if program == "zenity" and completed.returncode == ZENITY_TIMEOUT:
        return Answer(SKIPPED)
    if program == "kdialog" and completed.returncode == KDIALOG_CANCELLED:
        return Answer(CANCELLED)
    return Answer(UNAVAILABLE)


def ask_terminal(name: str, description: str) -> Answer:
    prompt = f"{name} ({description}), hidden: " if description else f"{name}, hidden: "
    try:
        value = getpass.getpass(prompt)
    except EOFError:
        return Answer(CANCELLED)
    return Answer(VALUE, value.encode("utf-8")) if value else Answer(SKIPPED)


def has_terminal() -> bool:
    return sys.stdin.isatty()


class Asker:
    """Reads secret values: a desktop dialog when asked for and available, else hidden
    terminal prompts, else nothing (the caller reports the owner action)."""

    def __init__(self, want_dialog: bool, target_label: str) -> None:
        self.want_dialog = want_dialog
        self.target_label = target_label
        self.dialog_failed = False

    def method(self) -> str | None:
        if self.want_dialog and not self.dialog_failed and dialog_program() is not None:
            return DIALOG
        if has_terminal():
            return TERMINAL
        return None

    def ask(self, name: str, description: str) -> Answer:
        method = self.method()
        if method == DIALOG:
            text = f"{description or 'Secret ' + name}\n\nFor {self.target_label}. It goes straight to the host."
            answer = ask_dialog(dialog_command(dialog_program() or "", name, text))
            if answer.outcome != UNAVAILABLE:
                return answer
            # The desktop refused the dialog (a session without a display): use the terminal.
            self.dialog_failed = True
            method = self.method()
        if method == TERMINAL:
            return ask_terminal(name, description)
        return Answer(UNAVAILABLE)


# -- output --------------------------------------------------------------------------------


@dataclass
class Result:
    args: object
    done: list[str] = field(default_factory=list)
    words: dict[str, str] = field(default_factory=dict)

    def line(self, name: str, word: str, detail: str = "") -> None:
        text = f"{name}: {word}{f' ({detail})' if detail else ''}"
        self.done.append(text)
        self.words[name] = word
        print(text, file=sys.stderr if self.args.json else sys.stdout, flush=True)

    def finish(self, owner_actions: list[str] | None = None, **extra) -> int:
        owner_actions = owner_actions or []
        code = EXIT_OWNER_ACTION if owner_actions else EXIT_DONE
        if self.args.json:
            document = {"status": "owner-action" if owner_actions else "done", "done": self.done,
                        "owner_actions": owner_actions, "gaps": [], "checks": [], **extra}
            if self.words:
                document["settings"] = self.words
            print(json.dumps(document, indent=2))
        else:
            for action in owner_actions:
                print(action)
        return code


def as_json(run):
    """With --json, errors also give one result document on stdout (Conventions)."""

    def wrapper(args) -> int:
        if not getattr(args, "json", False):
            return run(args)
        try:
            return run(args)
        except PakemError as error:
            message = str(error)
            statuses = {2: "usage", 3: "owner-action", 4: "gaps", 5: "check-failed", 6: "refused", 75: "busy"}
            actions = [line for line in message.splitlines() if line.startswith("OWNER ACTION REQUIRED")]
            print(json.dumps({"status": statuses.get(error.exit_code, "failed"), "done": [], "owner_actions": actions,
                              "gaps": [], "checks": [], "error": message}, indent=2))
            return error.exit_code

    return wrapper


def progress(args, text: str) -> None:
    print(text, file=sys.stderr if args.json else sys.stdout, flush=True)


def owner_command(args, project: Project, environment: str, words: list[str]) -> str:
    """The command an owner runs in their own terminal, with what this run was given."""
    command = ["pakem", *words]
    if args.environment:
        command += ["--environment", environment]
    if environment == PRODUCTION:
        command.append("--production")
    if Path.cwd().resolve() != project.root:
        command += ["--project-dir", str(project.root)]
    return shlex.join(command)


def age_text(seconds: int | None) -> str:
    if seconds is None:
        return ""
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''} old"
    return "just set"


# -- deploy keys on GitHub -----------------------------------------------------------------


def register_deploy_key(target: Target, name: str, public_key: str, write: bool, repository: str) -> None:
    """Register the host's new public key on the project's repository; write access only
    for deploy-key:write. The key file is temporary and removed afterwards."""
    title = f"{target.project.name}-{target.environment}-{name}"
    with tempfile.TemporaryDirectory(prefix="pakem-deploy-key-") as directory:
        key_file = Path(directory) / f"{name.lower()}.pub"
        key_file.write_text(public_key + "\n", encoding="utf-8")
        command = ["gh", "repo", "deploy-key", "add", str(key_file), "--repo", repository, "--title", title]
        if write:
            command.append("--allow-write")
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise CheckFailed(
            f"{name} is stored on {target.record.name}, but gh could not register its public key on {repository} "
            f"({detail[-1] if detail else f'exit {completed.returncode}'}). Add it as deploy key {title}"
            f"{' with write access' if write else ''}: {public_key}"
        )


def generate(target: Target, result: Result, name: str, kind: str, repository: str | None) -> None:
    if kind.startswith("token:"):
        target.send("secret-generate", [name], kind=kind)
        result.line(name, GENERATED, kind)
        return
    access = kind.split(":", 1)[1]
    reply = target.send("deploy-key", [name], kind=access)
    public_key = reply.get("public_key", "")
    if not public_key.startswith("ssh-ed25519 "):
        raise CheckFailed(f"{target.record.name} returned no public key for {name}")
    result.line(name, GENERATED, f"ed25519 deploy key, {access}")
    register_deploy_key(target, name, public_key, access == "write", repository or "")
    result.line(name, REGISTERED, f"{repository}, {'with write access' if access == 'write' else 'read-only'}")


# -- pakem secret set ----------------------------------------------------------------------


def _check_secret_args(args) -> None:
    batch = args.missing or args.generate_missing
    if batch and args.name:
        raise PakemError("--missing and --generate-missing work through every declared secret; name none")
    if not batch and not args.name:
        raise PakemError("name a secret, or pass --missing or --generate-missing")
    if args.dialog and (args.from_file or args.generate or args.generate_missing):
        raise PakemError("--dialog asks for values; it does not go with --from-file or --generate")


def read_value_file(path_text: str) -> bytes:
    path = Path(path_text).expanduser()
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise PakemError(f"cannot read {display_path(path)}: {exc.strerror}") from None
    # The engine's own secret-set drops trailing newlines the same way.
    value = value.rstrip(b"\n")
    if not value:
        raise PakemError(f"{display_path(path)} is empty")
    return value


@as_json
def run_secret_set(args) -> int:
    _check_secret_args(args)
    project, manifest, environment = prepare(args)
    if args.name:
        refuse_unless(manifest, args.name, SECRET)
        kind = args.generate
        if kind:
            refuse_kind_mismatch(manifest, args.name, kind)
        generating = {args.name: kind} if kind else {}
    elif args.generate_missing:
        generating = dict(manifest.generate)
    else:
        generating = {}
    require_production(args, environment)
    deploy_keys = any(kind.startswith("deploy-key:") for kind in generating.values())
    repository = github_repository(project) if deploy_keys else None
    asks = not generating and not args.from_file
    asker = Asker(args.dialog, f"{project.name} {environment}")
    if asks and not args.plan and args.name and asker.method() is None:
        raise OwnerActionRequired(
            f"OWNER ACTION REQUIRED: run `{owner_command(args, project, environment, ['secret', 'set', args.name])}` "
            "in your terminal"
        )
    target = connect(args, project, environment, NEEDS + (GITHUB_NEEDS if deploy_keys else ()), "pakem secret set")
    _print_secret_plan(args, target, generating, repository)
    if args.plan:
        progress(args, "Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem secret set")
    result = Result(args)
    if args.name:
        if generating:
            generate(target, result, args.name, generating[args.name], repository)
            return result.finish()
        if args.from_file:
            target.send("secret-set", [args.name], read_value_file(args.from_file))
            result.line(args.name, STORED)
            return result.finish()
        answer = asker.ask(args.name, manifest.descriptions.get(args.name, ""))
        if answer.outcome == UNAVAILABLE:
            command = owner_command(args, project, environment, ["secret", "set", args.name])
            return result.finish([f"OWNER ACTION REQUIRED: run `{command}` in your terminal"])
        _store_answer(target, result, args.name, answer)
        return result.finish()
    states = target.states(manifest)
    missing = [name for name in manifest.secrets if not states[name]["set"]]
    if args.generate_missing:
        for name in missing:
            if name in generating:
                generate(target, result, name, generating[name], repository)
        left = [name for name in missing if name not in generating]
        if not missing:
            progress(args, f"No declared secret is missing in {target.label}.")
        elif left:
            progress(args, f"Not generated (pakem cannot create them): {', '.join(left)}")
        return result.finish()
    asked = [name for name in missing if name not in manifest.generate]
    for name in missing:
        if name in manifest.generate:
            result.line(name, SKIPPED, "pakem creates it: `pakem secret set --generate-missing`")
    if not missing:
        progress(args, f"No declared secret is missing in {target.label}.")
    still_missing = []
    for name in asked:
        answer = asker.ask(name, manifest.descriptions.get(name, ""))
        if answer.outcome == UNAVAILABLE:
            still_missing = asked[asked.index(name):]
            break
        _store_answer(target, result, name, answer)
        if answer.outcome != VALUE:
            still_missing.append(name)
    if not still_missing:
        return result.finish()
    command = owner_command(args, project, environment, ["secret", "set", "--missing"])
    return result.finish([f"OWNER ACTION REQUIRED: run `{command}` in your terminal "
                          f"({', '.join(still_missing)} still missing)"])


def _store_answer(target: Target, result: Result, name: str, answer: Answer) -> None:
    if answer.outcome == VALUE:
        target.send("secret-set", [name], answer.value)
        result.line(name, STORED)
    else:
        result.line(name, answer.outcome)


def _print_secret_plan(args, target: Target, generating: dict[str, str], repository: str | None) -> None:
    how = "a hidden-input dialog on this desktop" if args.dialog else "hidden terminal input"
    if args.name:
        lines = [f"pakem secret set {args.name}: {target.label}"]
        if args.name in generating:
            lines.append(f"  create {args.name} on the host ({generating[args.name]})")
        elif args.from_file:
            lines.append(f"  read the value from {display_path(Path(args.from_file).expanduser())}")
        else:
            lines.append(f"  read the value from {how}")
    else:
        lines = [f"pakem secret set {'--generate-missing' if args.generate_missing else '--missing'}: {target.label}",
                 "  ask the host which declared secrets are not set"]
        if args.generate_missing:
            lines += [f"  create {name} on the host if missing ({kind})" for name, kind in generating.items()]
        else:
            lines.append(f"  read each missing one pakem cannot create from {how}, then hidden terminal input; "
                         "skip a cancelled one")
    for name, kind in generating.items():
        if kind.startswith("deploy-key:"):
            title = f"{target.project.name}-{target.environment}-{name}"
            access = "with write access" if kind.endswith(":write") else "read-only"
            lines.append(f"  register {name}'s public key on {repository} as deploy key {title} ({access})")
    lines.append(f"  sign each settings request with this device's key ({NAMESPACE}) and send it to "
                 f"{DEPLOY_USER}@{target.record.address}")
    progress(args, "\n".join(lines))


# -- pakem secret list, pakem placement, pakem settings status -------------------------------


@as_json
def run_secret_list(args) -> int:
    project, manifest, environment = prepare(args)
    target = connect(args, project, environment, NEEDS, "pakem secret list")
    states = target.states(manifest)
    rows = []
    for name in [*manifest.secrets, *manifest.host_generated]:
        state = states[name]
        rows.append({"name": name, "kind": manifest.kind(name), "state": "set" if state["set"] else "missing",
                     "age_seconds": state.get("age_seconds")})
    if args.json:
        return Result(args).finish(project=project.name, host=target.record.name, environment=environment,
                                   secrets=rows)
    width = max((len(row["name"]) for row in rows), default=0)
    print(target.label)
    for row in rows:
        detail = f"set, {age_text(row['age_seconds'])}" if row["state"] == "set" else "missing"
        note = " (host_generated)" if row["kind"] == HOST_GENERATED else ""
        print(f"  {row['name'].ljust(width)}  {detail}{note}")
    return EXIT_DONE


def parse_pairs(pairs: list[str], manifest: Manifest) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            # The text may be a value typed in the wrong place, so it is not repeated.
            raise PakemError("give each placement setting as NAME=VALUE")
        refuse_unless(manifest, name, PLACEMENT)
        if name in values:
            raise PakemError(f"{name} is given twice")
        if any(character in value for character in "\n\r\x00"):
            raise PakemError(f"{name}: the value must be a single line")
        values[name] = value
    return values


@as_json
def run_placement_set(args) -> int:
    project, manifest, environment = prepare(args)
    values = parse_pairs(args.pairs, manifest)
    require_production(args, environment)
    target = connect(args, project, environment, NEEDS, "pakem placement set")
    lines = [f"pakem placement set: {target.label}"]
    lines += [f"  {name}={value}" for name, value in values.items()]
    lines.append("  merge them into the host's placement.env (other names stay), signed with this device's key")
    progress(args, "\n".join(lines))
    if args.plan:
        progress(args, "Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem placement set")
    text = "".join(f"{name}={value}\n" for name, value in values.items())
    target.send("placement-set", list(values), text.encode("utf-8"))
    result = Result(args)
    for name in values:
        result.line(name, STORED)
    return result.finish()


@as_json
def run_placement_list(args) -> int:
    project, manifest, environment = prepare(args)
    target = connect(args, project, environment, NEEDS, "pakem placement list")
    states = target.states(manifest, with_values=args.values)
    rows = []
    for name in manifest.placement:
        row = {"name": name, "state": "set" if states[name]["set"] else "missing"}
        if args.values and "value" in states[name]:
            row["value"] = states[name]["value"]
        rows.append(row)
    if args.json:
        return Result(args).finish(project=project.name, host=target.record.name, environment=environment,
                                   placement=rows)
    width = max((len(row["name"]) for row in rows), default=0)
    print(target.label)
    for row in rows:
        value = f"  {row['value']}" if "value" in row else ""
        print(f"  {row['name'].ljust(width)}  {row['state']}{value}")
    return EXIT_DONE


def status_rows(manifest: Manifest, states: dict[str, dict]) -> list[dict]:
    rows = []
    for name in manifest.host_names:
        state = states[name]
        rows.append({
            "name": name, "kind": manifest.kind(name), "required": name in manifest.required,
            "state": "set" if state["set"] else "missing", "age_seconds": state.get("age_seconds"),
            "description": manifest.descriptions.get(name), "generate": manifest.generate.get(name),
        })
    return rows


def missing_required(target: Target, manifest: Manifest) -> list[str]:
    """Required placement and secret names the host has no value for; the engine refuses a
    deploy until they are set (it creates host_generated ones itself)."""
    states = target.states(manifest)
    return [name for name in [*manifest.placement, *manifest.secrets]
            if name in manifest.required and not states[name]["set"]]


@as_json
def run_settings_status(args) -> int:
    project, manifest, environment = prepare(args)
    target = connect(args, project, environment, NEEDS, "pakem settings status")
    rows = status_rows(manifest, target.states(manifest))
    missing = [row["name"] for row in rows if row["state"] == "missing" and row["required"]
               and row["kind"] != HOST_GENERATED]
    warnings = []
    if missing:
        warnings.append(f"required settings missing in {target.label}: {', '.join(missing)}. "
                        "The engine refuses a deploy until they are set")
    if manifest.undescribed():
        warnings.append(f"no [descriptions] entry in deploy/settings.toml for: {', '.join(manifest.undescribed())}")
    if args.json:
        checks = [{"name": "required settings", "ok": not missing,
                   "detail": f"missing: {', '.join(missing)}" if missing else "all set"}]
        print(json.dumps({"status": "done", "done": [], "owner_actions": [], "gaps": [], "checks": checks,
                          "project": project.name, "host": target.record.name, "environment": environment,
                          "settings": rows, "warnings": warnings}, indent=2))
        return EXIT_DONE
    print(target.label)
    width = max((len(row["name"]) for row in rows), default=0)
    for row in rows:
        state = row["state"]
        if state == "set" and row["age_seconds"] is not None:
            state += f", {age_text(row['age_seconds'])}"
        extras = [row["kind"], "required" if row["required"] else "optional", state]
        if row["generate"]:
            extras.append(f"pakem generates {row['generate']}")
        description = f": {row['description']}" if row["description"] else ""
        print(f"  {row['name'].ljust(width)}  {', '.join(extras)}{description}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    return EXIT_DONE
