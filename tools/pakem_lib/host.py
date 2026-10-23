"""`pakem setup host`, `pakem setup check --host`, and `pakem host runner add|remove`.

`pakem setup host <target> --project-dir DIR...` is the one owner step before releasing:
it makes this device's key if it has none, installs or updates the engine, and lets the
key release each project there (switching a project that deployed from a registry to
direct delivery), all over one connection.

Server administration signs in with the owner's own admin login and sudo, from the
owner's terminal only (ADR 0011, decision 8): agents never drive a personal identity, so
without a terminal these commands stop with an owner action and change nothing. The
device key is never used here; it may only deploy, through a forced command.

Each run opens one SSH connection (ControlMaster, so a password is asked at most once),
uploads the framework's deploy assets to a temporary directory on the host in the
checkout's layout (the bootstrap reads VERSION four levels up), runs the script there
with sudo, and removes the directory. What this device needs to reach a host again
lives in ~/.config/pakem/hosts.toml; what the host is lives on the host.
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    EXIT_DONE,
    EXIT_OWNER_ACTION,
    CheckFailed,
    CommandBusy,
    CommandRefused,
    Framework,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
    toml_string,
    toml_value,
    utc_now_iso,
    write_text_atomically,
)
from .device_config import config_dir
from .github import refuse_public_runner
from . import device_key
from .requirements import ensure
from .service_project import Project, authorize_arguments, is_service_project, read_project, setup_words
from .setup_device import create_device_key
from .source import WAIVER_DIRTY, WAIVER_UNMERGED, framework_source_status

DEPLOY_ASSETS = Path("skills") / "release-delivery" / "assets" / "deploy"
BOOTSTRAP = DEPLOY_ASSETS / "af_bootstrap.sh"
RUNNER = DEPLOY_ASSETS / "af_runner.sh"
# The bootstrap installs the runner script here; runner registrations use that copy.
INSTALLED_RUNNER = "/opt/af/af_runner.sh"
HOSTS_FILE = "hosts.toml"
HOST_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# An ~/.ssh/config alias or [user@]host; a leading "-" would be read as an ssh option.
SSH_TARGET = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:%\[\]-]*$")
DEPLOY_ROOT = re.compile(r"^/[A-Za-z0-9._/-]+$")
# The bootstrap's check exits 1 when a check fails and 2 on a usage or host error.
BOOTSTRAP_CHECK_FAILED = 1
CONTROL_PERSIST_SECONDS = 120
# A Unix socket path is limited to 104 bytes on macOS, and its TMPDIR alone is about 50, so
# the ControlMaster socket lives under /tmp (in a 0700 directory of its own).
CONTROL_PARENT = "/tmp"
DEPLOY_USER = "af"
# The receiver's exit codes (the engine's, contracts §5).
HOST_OK, HOST_REFUSED, HOST_BUSY = 0, 2, 75
HOST_RESULTS = {
    1: "failed; nothing to restore",
    3: "failed; the previous release was restored and verified",
    4: "failed, and restoring the previous release failed too",
    5: "failed after a schema change; services are stopped for you",
}
SSH_FAILED = 255
# A POSIX shell's exit code for a command it cannot find.
SHELL_NOT_FOUND = 127


@dataclass
class HostRecord:
    name: str
    target: str
    port: int | None = None
    deploy_root: str = "/srv/deploy"
    trigger: str = "pull"
    proxy: bool = True
    runner: bool = False
    # OpenSSH's port for releases when port 22 skips forced commands (Tailscale SSH).
    release_port: int | None = None
    # Projects this device may release there (`pakem host allow-device`).
    projects: list[str] = field(default_factory=list)
    # Service projects on this device to allow there on the next `pakem setup host`.
    project_dirs: list[str] = field(default_factory=list)
    set_up_at: str | None = None

    @property
    def address(self) -> str:
        return self.target.rsplit("@", 1)[-1]

    def bootstrap_flags(self) -> list[str]:
        flags = ["--trigger", self.trigger, "--deploy-root", self.deploy_root]
        if not self.proxy:
            flags.append("--no-proxy")
        if self.release_port is not None:
            flags += ["--release-port", str(self.release_port)]
        return flags


def hosts_path() -> Path:
    return config_dir() / HOSTS_FILE


def load_hosts() -> dict[str, HostRecord]:
    path = hosts_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    hosts = {}
    for name, table in data.get("hosts", {}).items():
        keys = ("target", "port", "deploy_root", "trigger", "proxy", "runner", "release_port", "projects",
                "project_dirs", "set_up_at")
        fields = {key: table[key] for key in keys if key in table}
        hosts[name] = HostRecord(name, **fields)
    return hosts


def save_host(record: HostRecord, set_up: bool = True) -> None:
    """Record a host; `set_up` stamps it as just set up (`pakem host add` only records it)."""
    hosts = load_hosts()
    if set_up:
        record.set_up_at = utc_now_iso()
    hosts[record.name] = record
    lines = ["# Written by `pakem host add`, `pakem setup host`, and `pakem host allow-device`; device-local."]
    for name in sorted(hosts):
        host = hosts[name]
        lines += ["", f"[hosts.{toml_string(name)}]", f"target = {toml_value(host.target)}"]
        if host.port is not None:
            lines.append(f"port = {host.port}")
        lines += [
            f"deploy_root = {toml_value(host.deploy_root)}",
            f"trigger = {toml_value(host.trigger)}",
            f"proxy = {toml_value(host.proxy)}",
            f"runner = {toml_value(host.runner)}",
        ]
        if host.release_port is not None:
            lines.append(f"release_port = {host.release_port}")
        if host.projects:
            lines.append(f"projects = {toml_value(sorted(host.projects))}")
        if host.project_dirs:
            lines.append(f"project_dirs = {toml_value(host.project_dirs)}")
        if host.set_up_at:
            lines.append(f"set_up_at = {toml_value(host.set_up_at)}")
    write_text_atomically(hosts_path(), "\n".join(lines) + "\n")


def default_name(target: str) -> str:
    host = target.rsplit("@", 1)[-1].lower()
    if not HOST_NAME.match(host):
        raise PakemError(f"cannot derive a host name from {target!r}; pass --name")
    return host


def resolve_host(name_or_target: str) -> HostRecord:
    hosts = load_hosts()
    if name_or_target in hosts:
        return hosts[name_or_target]
    for record in hosts.values():
        if record.target == name_or_target:
            return record
    raise PakemError(
        f"no host {name_or_target!r} in {display_path(hosts_path())}; set it up first with "
        f"`pakem setup host {name_or_target}`"
    )


class Remote:
    """One multiplexed SSH connection to a host, signed in with the owner's own login."""

    def __init__(self, record: HostRecord, control_dir: Path) -> None:
        self.record = record
        self.control = control_dir / "%C"

    def _command(self, remote: str, tty: bool) -> list[str]:
        command = [
            "ssh",
            "-o", f"ControlPath={self.control}",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPersist={CONTROL_PERSIST_SECONDS}",
        ]
        if self.record.port is not None:
            command += ["-p", str(self.record.port)]
        if tty:
            command.append("-t")
        return [*command, self.record.target, remote]

    def run(self, remote: str, tty: bool = False, data: bytes | None = None) -> subprocess.CompletedProcess:
        """Run a command on the host; with tty, sudo can ask for the password on this terminal."""
        if tty:
            return subprocess.run(self._command(remote, tty))
        return subprocess.run(self._command(remote, tty), input=data, capture_output=True)

    def output(self, remote: str, data: bytes | None = None) -> str:
        completed = self.run(remote, data=data)
        if completed.returncode != 0:
            detail = completed.stderr.decode(errors="replace").strip().splitlines()
            raise PakemError(f"`{remote}` failed on {self.record.target}: {detail[-1] if detail else completed.returncode}")
        return completed.stdout.decode().strip()

    def close(self) -> None:
        subprocess.run(
            ["ssh", "-o", f"ControlPath={self.control}", "-O", "exit", self.record.target],
            capture_output=True,
        )


def asset_bundle(framework: Framework) -> bytes:
    """VERSION and the deploy assets, in the checkout's layout, as an uncompressed tar."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        bundle.add(framework.version_path, arcname="VERSION")
        source = framework.root / DEPLOY_ASSETS
        for path in sorted(source.rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            bundle.add(path, arcname=str(DEPLOY_ASSETS / path.relative_to(source)))
    return buffer.getvalue()


def upload_assets(remote: Remote, framework: Framework) -> str:
    directory = remote.output("mktemp -d /tmp/pakem-host.XXXXXX")
    try:
        remote.output(f"tar -xf - -C {shlex.quote(directory)}", data=asset_bundle(framework))
    except PakemError:
        remote.run(f"rm -rf {shlex.quote(directory)}")
        raise
    return directory


def run_sudo(remote: Remote, directory: str, script: Path, arguments: list[str]) -> int:
    command = shlex.join(["sudo", "bash", f"{directory}/{script}", *arguments])
    return remote.run(command, tty=True).returncode


def require_terminal(command_line: str) -> None:
    if not sys.stdin.isatty():
        raise OwnerActionRequired(
            f"OWNER ACTION REQUIRED: run `{command_line}` in your terminal; it signs in with your own admin "
            "login, and sudo asks for your password there"
        )


HOST_OPTIONS = ("port", "deploy_root", "trigger", "proxy", "runner", "release_port")


def record_from_args(args) -> HostRecord:
    """The recorded host the arguments name, with only the given options changed; or a new
    host with defaults for the rest. A re-run therefore needs no flags at all."""
    hosts = load_hosts()
    if args.name:
        known = hosts.get(args.name)
    else:
        # The argument is a recorded name, or the target of exactly one recorded host.
        by_target = [record for record in hosts.values() if record.target == args.target]
        known = hosts.get(args.target) or (by_target[0] if len(by_target) == 1 else None)
    if known is not None:
        record = dataclasses.replace(known, projects=list(known.projects), project_dirs=list(known.project_dirs))
        if args.target and args.target not in (known.name, known.target):
            record.target = args.target
    elif args.target:
        record = HostRecord(args.name or default_name(args.target), args.target)
    else:
        raise PakemError("name a host: `pakem setup host <name>`; a new one needs its SSH target, too")
    for option in HOST_OPTIONS:
        value = getattr(args, option, None)
        if value is not None:
            setattr(record, option, value)
    for directory in args.project_dir or []:
        resolved = str(Path(directory).expanduser().resolve())
        read_project(Path(resolved))
        if resolved not in record.project_dirs:
            record.project_dirs.append(resolved)
    if not SSH_TARGET.match(record.target):
        raise PakemError(f"invalid SSH target {record.target!r}: an alias or user@address, never starting with -")
    if not DEPLOY_ROOT.match(record.deploy_root) or record.deploy_root == "/":
        raise PakemError("--deploy-root must be an absolute path other than /")
    if record.release_port is not None:
        check_release_port(record)
    if not HOST_NAME.match(record.name):
        raise PakemError(f"invalid host name {record.name!r}: lowercase letters, digits, dots, and hyphens")
    return record


def login_port(record: HostRecord) -> int:
    """The port the owner logs in on: the recorded one, or what ~/.ssh/config gives the
    target (`ssh -G` reads the configuration only; it never connects)."""
    if record.port is not None:
        return record.port
    completed = subprocess.run(["ssh", "-G", record.target], capture_output=True, text=True)
    for line in completed.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "port" and value.isdigit():
            return int(value)
    return 22


def check_release_port(record: HostRecord) -> None:
    """The release port admits only the deploy user, so it must never be the login port."""
    port = record.release_port
    if not 0 < port < 65536:
        raise PakemError(f"--release-port must be a TCP port, not {port}")
    if port == login_port(record):
        raise PakemError(
            f"--release-port {port} is the port you log in on ({record.target}); only the deploy user could log "
            "in there afterwards. Choose a free port, such as 2223"
        )


def setup_flags(record: HostRecord) -> list[str]:
    """Every option, spelled out (for a host that is not recorded on this device yet)."""
    words = ["--name", record.name]
    if record.port is not None:
        words += ["--port", str(record.port)]
    words += ["--trigger", record.trigger, "--deploy-root", record.deploy_root]
    if not record.proxy:
        words.append("--no-proxy")
    if record.runner:
        words.append("--runner")
    if record.release_port is not None:
        words += ["--release-port", str(record.release_port)]
    return words


def setup_host_command(record: HostRecord, project_roots: list[Path] | None = None) -> str:
    """What the owner runs on a terminal: the short form once the host is recorded here."""
    recorded = load_hosts().get(record.name)
    if recorded is not None and recorded == record and not project_roots:
        return f"pakem setup host {record.name}"
    return setup_words(record.target, setup_flags(record), project_roots or [])


def _projects(record: HostRecord) -> list[Project]:
    """The projects to allow: those recorded for the host, or the current directory if it is
    a service project; a recorded directory missing on this device is skipped with a note."""
    directories = list(record.project_dirs)
    if not directories and is_service_project(Path.cwd()):
        directories = [str(Path.cwd().resolve())]
        record.project_dirs = directories
    projects = []
    for directory in directories:
        if not is_service_project(Path(directory)):
            print(f"  note: {directory} is no service project on this device any more; skipped")
            continue
        projects.append(read_project(Path(directory)))
    return projects


def interview(args) -> None:
    """On a terminal, `pakem setup host` with no host asks for what it needs."""
    def ask(question: str, default: str = "") -> str:
        shown = f" [{default}]" if default else ""
        return input(f"{question}{shown}: ").strip() or default

    def yes(question: str, default: bool) -> bool:
        return ask(f"{question} (y/n)", "y" if default else "n").lower().startswith("y")

    args.target = ask("SSH target of the server (an alias in ~/.ssh/config, or user@address)")
    if not args.target:
        raise PakemError("a server's SSH target is needed")
    args.name = ask("Name for it on this device", default_name(args.target))
    args.proxy = not yes("Is it private (no public web traffic, no Caddy)?", False)
    args.runner = yes("Does it run (or should it run) the CI runner for private repositories?", False)
    if yes("Is its port 22 answered by Tailscale SSH (or anything that skips authorized_keys)?", False):
        answer = ask("Free port for releases (not the one you log in on)", "2223")
        if not answer.isdigit():
            raise PakemError(f"a release port is a number, not {answer!r}")
        args.release_port = int(answer)
    args.deploy_root = ask("Deploy root on the server", "/srv/deploy")
    here = [str(Path.cwd())] if is_service_project(Path.cwd()) else []
    answer = ask("Service projects on this device to release there (comma-separated paths)", ",".join(here))
    args.project_dir = [part.strip() for part in answer.split(",") if part.strip()]


def run_host_add(args) -> int:
    """Record a host's options on this device only; no connection is made."""
    record = record_from_args(args)
    projects = ", ".join(Path(directory).name for directory in record.project_dirs) or "none yet"
    if args.plan:
        print(f"would record host {record.name} ({record.target}) in {display_path(hosts_path())}: "
              f"{shlex.join(setup_flags(record))}; projects: {projects}")
        print("Plan only: nothing changed.")
        return EXIT_DONE
    save_host(record, set_up=False)
    print(f"Recorded host {record.name} ({record.target}) in {display_path(hosts_path())}; projects: {projects}.")
    print(f"OWNER ACTION REQUIRED: run `pakem setup host {record.name}` in your terminal (your own login)")
    return EXIT_DONE


def _check_source(framework: Framework, args) -> str:
    """Only merged main or a release reaches a server, as with `pakem install` (source.py)."""
    status = framework_source_status(framework.root)
    waived = {WAIVER_DIRTY: args.allow_dirty, WAIVER_UNMERGED: args.allow_unmerged}
    refusals = [
        f"{finding.message} (pass {finding.waiver} to use it anyway)"
        for finding in status.findings
        if not waived[finding.waiver]
    ]
    if refusals:
        raise CommandRefused("refusing to upload deploy assets from this checkout:\n  " + "\n  ".join(refusals))
    return status.summary


def run_setup_host(args) -> int:
    framework = Framework(Path(args.root))
    if args.target is None and args.name is None:
        if not sys.stdin.isatty():
            raise PakemError("name a host: `pakem setup host <name>` (agents record a new one with `pakem host add`)")
        interview(args)
    record = record_from_args(args)
    projects = _projects(record)
    source = _check_source(framework, args)
    install = shlex.join(["sudo", "bash", str(BOOTSTRAP), "install", *record.bootstrap_flags()])
    steps = []
    if projects and not device_key.private_path().exists():
        steps.append("create this device's release key (ssh-keygen asks for its passphrase here)")
    steps += [
        f"sign in to {record.target} with your own login (one connection; a password is asked at most once)",
        f"upload the deploy assets of Pakem {framework.version()} to a temporary directory there",
        install,
    ]
    if record.runner:
        steps.append(f"sudo bash {RUNNER} install")
    steps.append("remove the temporary directory")
    for project in projects:
        steps.append(
            f"let this device's key release {project.name} ({', '.join(project.environments)}); an environment "
            "that deploys from a registry moves to direct delivery, keeping its settings, secrets, and backups"
        )
    steps.append(f"record host {record.name} in {display_path(hosts_path())}")
    print(f"pakem setup host {record.target} (framework {source})")
    for step in steps:
        print(f"  {step}")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    if not sys.stdin.isatty():
        # An agent: keep what it was told here, so the owner's step is the short command.
        save_host(record, set_up=False)
    require_terminal(f"pakem setup host {record.name}")
    confirm_steps(args.yes, "pakem setup host")
    if projects and create_device_key(passphrase=not args.no_passphrase) == EXIT_OWNER_ACTION:
        raise OwnerActionRequired(
            f"OWNER ACTION REQUIRED: the device key exists but is not in ssh-agent; add it as shown above, then run "
            f"`pakem setup host {record.name}` again"
        )
    allowed = []
    with tempfile.TemporaryDirectory(prefix="pk-", dir=CONTROL_PARENT) as control_dir:
        remote = Remote(record, Path(control_dir))
        try:
            directory = upload_assets(remote, framework)
            try:
                code = run_sudo(remote, directory, BOOTSTRAP, ["install", *record.bootstrap_flags()])
                if code == 0 and record.runner:
                    code = run_sudo(remote, directory, RUNNER, ["install"])
            finally:
                remote.output(f"rm -rf {shlex.quote(directory)}")
            for project in projects if code == 0 else []:
                arguments = authorize_arguments(project, record.deploy_root, record.proxy, switch_from_registry=True)
                if remote.run(shlex.join(arguments), tty=True).returncode != 0:
                    raise PakemError(f"allowing this device to release {project.name} failed; see the lines above")
                allowed.append(project.name)
        finally:
            remote.close()
    record.projects = sorted(set(record.projects) | set(allowed))
    if code == BOOTSTRAP_CHECK_FAILED:
        save_host(record, set_up=False)
        raise CheckFailed(f"{record.target} is installed but its check failed; see the lines above")
    if code != 0:
        raise PakemError(f"host setup failed on {record.target} with exit {code}; see the lines above")
    save_host(record)
    print(f"Host {record.name} is ready; recorded in {display_path(hosts_path())}.")
    for name in allowed:
        # The device key must reach the receiver, not a shell (see DeviceLink.failure).
        DeviceLink(record).query(f"status {name}")
    if allowed:
        print(f"This device may release {', '.join(allowed)} there (checked with its key): `pakem release` runs "
              "without your login now.")
    return EXIT_DONE


def run_host_check(args, name_or_target: str) -> int:
    framework = Framework(Path(args.root))
    record = resolve_host(name_or_target)
    require_terminal(f"pakem setup check --host {name_or_target}")
    with tempfile.TemporaryDirectory(prefix="pk-", dir=CONTROL_PARENT) as control_dir:
        remote = Remote(record, Path(control_dir))
        try:
            directory = upload_assets(remote, framework)
            try:
                code = run_sudo(remote, directory, BOOTSTRAP, ["check", *record.bootstrap_flags()])
                if record.runner:
                    runner_code = run_sudo(remote, directory, RUNNER, ["check"])
                    if code == 0:
                        code = runner_code
            finally:
                remote.output(f"rm -rf {shlex.quote(directory)}")
        finally:
            remote.close()
    if code == BOOTSTRAP_CHECK_FAILED:
        raise CheckFailed(f"{record.name}: a host check failed; see the lines above")
    if code != 0:
        raise PakemError(f"host check could not run on {record.target} (exit {code})")
    return EXIT_DONE


def _runner_token(repository: str, action: str) -> bytes:
    """A short-lived token from the owner's gh login; kept in memory, never printed."""
    endpoint = "registration-token" if action == "add" else "remove-token"
    completed = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{repository}/actions/runners/{endpoint}", "--jq", ".token"],
        capture_output=True,
    )
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        detail = completed.stderr.decode(errors="replace").strip().splitlines()
        raise PakemError(f"gh could not get a runner {endpoint} for {repository}: {detail[-1] if detail else 'empty'}")
    return token


def run_host_runner(args) -> int:
    framework = Framework(Path(args.root))
    if not REPOSITORY.match(args.repository):
        raise PakemError(f"repository must look like owner/name, not {args.repository!r}")
    record = resolve_host(args.host)
    if not record.runner:
        raise CommandRefused(
            f"{record.name} has no CI runner; set it up first with `pakem setup host {record.target} --runner`"
        )
    verb = "add-repo" if args.runner_command == "add" else "remove-repo"
    ensure(framework, ("gh", "github-login"), args, f"pakem host runner {args.runner_command}")
    if args.runner_command == "add":
        refuse_public_runner(args.repository)
    print(f"  get a runner token for {args.repository} from your gh login (never printed)")
    print(f"  sudo {INSTALLED_RUNNER} {verb} {args.repository} on {record.target} (token on standard input)")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    require_terminal(f"pakem host runner {args.runner_command} {args.host} {args.repository}")
    confirm_steps(args.yes, f"pakem host runner {args.runner_command}")
    token = _runner_token(args.repository, args.runner_command)
    with tempfile.TemporaryDirectory(prefix="pk-", dir=CONTROL_PARENT) as control_dir:
        remote = Remote(record, Path(control_dir))
        try:
            # The token waits in a file only the login user can read, so sudo can still ask
            # for the password on the terminal; the file is removed whatever happens.
            token_file = remote.output('umask 077 && f="$(mktemp)" && cat > "$f" && printf %s "$f"', data=token)
            try:
                command = f"sudo {INSTALLED_RUNNER} {verb} {shlex.quote(args.repository)} < {shlex.quote(token_file)}"
                code = remote.run(command, tty=True).returncode
            finally:
                remote.output(f"rm -f {shlex.quote(token_file)}")
        finally:
            remote.close()
    if code != 0:
        raise PakemError(f"runner {verb} failed on {record.target} with exit {code}; see the lines above")
    print(f"Runner {args.runner_command} done for {args.repository} on {record.name}.")
    return EXIT_DONE


class DeviceLink:
    """SSH as the deploy user with the device key; the host's forced command answers."""

    def __init__(self, record: HostRecord) -> None:
        self.record = record

    def command(self, remote: str) -> list[str]:
        command = [
            "ssh", "-i", str(device_key.private_path()), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        ]
        port = self.record.release_port or self.record.port
        if port is not None:
            command += ["-p", str(port)]
        return [*command, f"{DEPLOY_USER}@{self.record.address}", remote]

    def query(self, remote: str) -> dict:
        completed = subprocess.run(self.command(remote), stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if completed.returncode != HOST_OK:
            raise self.failure(completed.returncode, (completed.stdout + completed.stderr).strip(), remote)
        try:
            return json.loads(completed.stdout)
        except ValueError as exc:
            raise PakemError(f"{self.record.name} answered `{remote}` with something other than JSON") from exc

    def send(self, payload: Path, quiet: bool) -> int:
        """Send a signed request; the host's progress goes to this terminal (stderr with --json)."""
        with payload.open("rb") as stdin:
            completed = subprocess.run(self.command("release"), stdin=stdin, stdout=sys.stderr if quiet else None)
        return completed.returncode

    def failure(self, code: int, detail: str = "", operation: str = "release") -> PakemError:
        last = detail.splitlines()[-1] if detail else "see the lines above"
        if code == SHELL_NOT_FOUND:
            # A shell ran the request: something answered before OpenSSH's forced command,
            # such as Tailscale SSH on port 22, which logs in by tailnet identity.
            return CheckFailed(
                f"{self.record.name} gave the device key a shell instead of the release receiver ({last}); "
                "something on that port skips authorized_keys forced commands (Tailscale SSH does). Set up a "
                f"release port: `{setup_host_command(self.record)} --release-port 2222`"
            )
        if code == SSH_FAILED and self.record.release_port is not None:
            return PakemError(
                f"could not reach {DEPLOY_USER}@{self.record.address} on release port {self.record.release_port} "
                f"with the device key ({last}); check that only sshd listens there "
                f"(`sudo ss -ltnp 'sport = :{self.record.release_port}'` on the host), or choose a free port with "
                f"`{setup_host_command(self.record)}` and another --release-port"
            )
        if code == SSH_FAILED:
            return PakemError(
                f"could not reach {DEPLOY_USER}@{self.record.address} with the device key ({last}); if this device "
                f"was never allowed there, run `{setup_host_command(self.record)} --project-dir <project>` in your "
                "terminal"
            )
        if code == HOST_REFUSED:
            return CommandRefused(f"{self.record.name} refused: {last}")
        if code == HOST_BUSY:
            return CommandBusy(f"{self.record.name}: another deployment holds the lock; try again shortly")
        if operation != "release":
            return CheckFailed(f"{self.record.name}: `{operation}` failed with exit {code}: {last}")
        return CheckFailed(f"{self.record.name}: release {HOST_RESULTS.get(code, f'failed with exit {code}')}")
