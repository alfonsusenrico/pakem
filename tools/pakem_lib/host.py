"""`pakem setup host`, `pakem setup check --host`, and `pakem host runner add|remove`.

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
    CheckFailed,
    CommandRefused,
    Framework,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
    utc_now_iso,
    write_text_atomically,
)
from .device_config import config_dir
from .requirements import ensure
from .source import WAIVER_DIRTY, WAIVER_UNMERGED, framework_source_status

DEPLOY_ASSETS = Path("skills") / "release-delivery" / "assets" / "deploy"
BOOTSTRAP = DEPLOY_ASSETS / "af_bootstrap.sh"
RUNNER = DEPLOY_ASSETS / "af_runner.sh"
# The bootstrap installs the runner script here; runner registrations use that copy.
INSTALLED_RUNNER = "/opt/af/af_runner.sh"
HOSTS_FILE = "hosts.toml"
HOST_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEPLOY_ROOT = re.compile(r"^/[A-Za-z0-9._/-]+$")
# The bootstrap's check exits 1 when a check fails and 2 on a usage or host error.
BOOTSTRAP_CHECK_FAILED = 1
CONTROL_PERSIST_SECONDS = 120
# A Unix socket path is limited to 104 bytes on macOS, and its TMPDIR alone is about 50, so
# the ControlMaster socket lives under /tmp (in a 0700 directory of its own).
CONTROL_PARENT = "/tmp"


@dataclass
class HostRecord:
    name: str
    target: str
    port: int | None = None
    deploy_root: str = "/srv/deploy"
    trigger: str = "pull"
    proxy: bool = True
    runner: bool = False
    # Projects this device may release there (`pakem host allow-device`).
    projects: list[str] = field(default_factory=list)

    @property
    def address(self) -> str:
        return self.target.rsplit("@", 1)[-1]

    def bootstrap_flags(self) -> list[str]:
        flags = ["--trigger", self.trigger, "--deploy-root", self.deploy_root]
        if not self.proxy:
            flags.append("--no-proxy")
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
        keys = ("target", "port", "deploy_root", "trigger", "proxy", "runner", "projects")
        fields = {key: table[key] for key in keys if key in table}
        hosts[name] = HostRecord(name, **fields)
    return hosts


def save_host(record: HostRecord) -> None:
    hosts = load_hosts()
    hosts[record.name] = record
    lines = ["# Written by `pakem setup host` and `pakem host allow-device`; device-local, never committed."]
    for name in sorted(hosts):
        host = hosts[name]
        lines += ["", f"[hosts.{json.dumps(name)}]", f"target = {json.dumps(host.target)}"]
        if host.port is not None:
            lines.append(f"port = {host.port}")
        lines += [
            f"deploy_root = {json.dumps(host.deploy_root)}",
            f"trigger = {json.dumps(host.trigger)}",
            f"proxy = {json.dumps(host.proxy)}",
            f"runner = {json.dumps(host.runner)}",
        ]
        if host.projects:
            lines.append(f"projects = {json.dumps(sorted(host.projects))}")
        if name == record.name:
            lines.append(f"set_up_at = {json.dumps(utc_now_iso())}")
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
    remote.output(f"tar -xf - -C {shlex.quote(directory)}", data=asset_bundle(framework))
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


def _record_from_args(args) -> HostRecord:
    if not DEPLOY_ROOT.match(args.deploy_root) or args.deploy_root == "/":
        raise PakemError("--deploy-root must be an absolute path other than /")
    name = args.name or default_name(args.target)
    if not HOST_NAME.match(name):
        raise PakemError(f"invalid host name {name!r}: lowercase letters, digits, dots, and hyphens")
    return HostRecord(name, args.target, args.port, args.deploy_root, args.trigger, not args.no_proxy, args.runner)


def setup_host_command(record: HostRecord) -> str:
    """The command line that repeats this setup, for the owner to run on a terminal."""
    words = ["pakem", "setup", "host", record.target, "--name", record.name]
    if record.port is not None:
        words += ["--port", str(record.port)]
    words += ["--trigger", record.trigger, "--deploy-root", record.deploy_root]
    if not record.proxy:
        words.append("--no-proxy")
    if record.runner:
        words.append("--runner")
    return shlex.join(words)


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
    record = _record_from_args(args)
    known = load_hosts().get(record.name)
    if known is not None:
        record.projects = known.projects
    source = _check_source(framework, args)
    install = shlex.join(["sudo", "bash", str(BOOTSTRAP), "install", *record.bootstrap_flags()])
    steps = [
        f"sign in to {record.target} with your own login (one connection; a password is asked at most once)",
        f"upload the deploy assets of Pakem {framework.version()} to a temporary directory there",
        install,
    ]
    if record.runner:
        steps.append(f"sudo bash {RUNNER} install")
    steps += ["remove the temporary directory", f"record host {record.name} in {display_path(hosts_path())}"]
    print(f"pakem setup host {record.target} (framework {source})")
    for step in steps:
        print(f"  {step}")
    if args.plan:
        print("Plan only: nothing changed.")
        return EXIT_DONE
    require_terminal(setup_host_command(record))
    confirm_steps(args.yes, "pakem setup host")
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
        finally:
            remote.close()
    if code == BOOTSTRAP_CHECK_FAILED:
        save_host(record)
        raise CheckFailed(f"{record.target} is installed but its check failed; see the lines above")
    if code != 0:
        raise PakemError(f"host setup failed on {record.target} with exit {code}; see the lines above")
    save_host(record)
    print(f"Host {record.name} is ready; recorded in {display_path(hosts_path())}.")
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
