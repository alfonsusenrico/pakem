"""What pakem commands need on this device, prepared when a command needs it.

Each command declares its needs by name (NEED_ORDER). `assess` checks them without
changing anything; `prepare` prints what is missing and how pakem provides it, asks one
confirmation (`--yes` skips it), does it, and checks again.

Approval (ADR 0011, decision 8): the owner's instruction in the session to run a
command approves the installs it lists; there is no standing approval. What only the
owner can do comes back as an owner action with the exact instruction (exit 3):
browser logins, sudo without a terminal (an agent cannot type the password), the device
key's passphrase, and anything the package manager cannot provide.

Installs use Homebrew on macOS and apt on Debian, Ubuntu, and WSL. Any container runtime
that answers `docker info` is accepted; Colima is installed and started on macOS only
when none does.
"""

from __future__ import annotations

import json
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import device_key
from .common import (
    EXIT_CHECK_FAILED,
    EXIT_DONE,
    EXIT_OWNER_ACTION,
    CheckFailed,
    CommandRefused,
    Framework,
    OwnerActionRequired,
    PakemError,
    confirm_steps,
    display_path,
    write_text_atomically,
)
from .device_config import config_dir
from .manifest import load_manifest

MACOS = "macos"
LINUX = "linux"
DEVICE_FILE = "device.toml"
PROBE_TIMEOUT_SECONDS = 20
# The framework install runs as its own command, with the same tool that runs this one.
RUNNING_TOOL = Path(__file__).resolve().parent.parent / "pakem"
SETUP_KEY_ACTION = (
    "OWNER ACTION REQUIRED: run `pakem setup key` in your terminal; it asks for the key's "
    "passphrase (add --no-passphrase only for a device nobody can unlock, such as a headless machine)"
)


@dataclass(frozen=True)
class Device:
    system: str
    wsl: bool
    arch: str

    @property
    def label(self) -> str:
        if self.wsl:
            return f"{self.system} (WSL), {self.arch}"
        return f"{self.system}, {self.arch}"


@dataclass(frozen=True)
class Tool:
    name: str
    version_command: tuple[str, ...]
    brew: str | None
    apt: str | None
    manual: str


TOOLS = {
    "git": Tool("git", ("git", "--version"), "git", "git", "https://git-scm.com/downloads"),
    "ssh": Tool("ssh", ("ssh", "-V"), "openssh", "openssh-client", "https://www.openssh.com/portable.html"),
    "gh": Tool("gh", ("gh", "--version"), "gh", "gh", "https://github.com/cli/cli#installation"),
    "glab": Tool("glab", ("glab", "--version"), "glab", "glab", "https://gitlab.com/gitlab-org/cli#installation"),
    "uv": Tool("uv", ("uv", "--version"), "uv", None, "https://docs.astral.sh/uv/getting-started/installation/"),
    "docker": Tool("docker", ("docker", "--version"), "docker", "docker.io", "https://docs.docker.com/engine/install/"),
}
COLIMA = Tool("colima", ("colima", "version"), "colima", None, "https://github.com/abiosoft/colima")
LOGINS = {"github-login": ("gh", "GitHub"), "gitlab-login": ("glab", "GitLab")}
NEED_ORDER = (
    *TOOLS,
    "container-runtime",
    "device-key",
    "device-key-unlocked",
    *LOGINS,
    "framework",
    "device-settings",
)
DEVICE_NEEDS = NEED_ORDER


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class Step:
    description: str
    run: Callable[[], None]


@dataclass
class Assessment:
    device: Device
    interactive: bool
    checks: list[Check] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    owner_actions: list[str] = field(default_factory=list)
    creates_key: bool = False

    def outcome(self) -> int:
        if self.owner_actions:
            return EXIT_OWNER_ACTION
        if all(check.ok for check in self.checks):
            return EXIT_DONE
        return EXIT_CHECK_FAILED


def detect_device() -> Device:
    name = platform.system()
    if name == "Darwin":
        return Device(MACOS, False, platform.machine())
    if name == "Linux":
        try:
            kernel = Path("/proc/version").read_text(encoding="utf-8").lower()
        except OSError:
            kernel = ""
        return Device(LINUX, "microsoft" in kernel, platform.machine())
    if name == "Windows":
        raise CommandRefused("native Windows is not supported; run pakem inside WSL")
    raise CommandRefused(f"unsupported system {name}; pakem supports macOS, Linux, and WSL")


def device_file() -> Path:
    return config_dir() / DEVICE_FILE


def device_name() -> str:
    return socket.gethostname().split(".")[0]


def _probe(command: tuple[str, ...]) -> subprocess.CompletedProcess | None:
    """Run a read-only probe; None when the program is missing or does not answer in time."""
    if shutil.which(command[0]) is None:
        return None
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def _version(tool: Tool) -> str | None:
    completed = _probe(tool.version_command)
    if completed is None or completed.returncode != 0:
        return None
    lines = (completed.stdout or completed.stderr).strip().splitlines()
    if lines:
        return lines[0]
    return "installed"


def _manager(device: Device) -> str:
    if device.system == MACOS:
        return "brew"
    return "apt-get"


def _apt_has(package: str) -> bool:
    completed = _probe(("apt-cache", "policy", package))
    if completed is None or completed.returncode != 0:
        return False
    return "Candidate:" in completed.stdout and "Candidate: (none)" not in completed.stdout


def _package_for(tool: Tool, device: Device) -> str | None:
    if shutil.which(_manager(device)) is None:
        return None
    if device.system == MACOS:
        return tool.brew
    if tool.apt is not None and _apt_has(tool.apt):
        return tool.apt
    return None


def _install_commands(assessment: Assessment) -> list[list[str]]:
    if assessment.device.system == MACOS:
        return [["brew", "install", *assessment.packages]]
    return [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", *assessment.packages]]


def _can_sudo(assessment: Assessment) -> bool:
    """sudo works when someone can type the password, or when it needs none."""
    if assessment.interactive:
        return True
    probe = _probe(("sudo", "-n", "true"))
    return probe is not None and probe.returncode == 0


def _run(command: list[str]) -> None:
    # Installers ask for passwords and print progress, so they share this terminal.
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise PakemError(f"`{' '.join(command)}` failed with exit {completed.returncode}")


def _need_tool(assessment: Assessment, name: str) -> None:
    tool = TOOLS[name]
    version = _version(tool)
    if version is not None:
        assessment.checks.append(Check(name, True, version))
        return
    package = _package_for(tool, assessment.device)
    if package is not None:
        assessment.packages.append(package)
        assessment.checks.append(Check(name, False, f"missing; pakem installs package {package}"))
        return
    assessment.checks.append(Check(name, False, "missing"))
    assessment.owner_actions.append(f"OWNER ACTION REQUIRED: install {name} ({tool.manual})")


def _need_runtime(assessment: Assessment) -> None:
    info = _probe(("docker", "info", "--format", "{{.OperatingSystem}}"))
    if info is not None and info.returncode == 0:
        assessment.checks.append(Check("container runtime", True, info.stdout.strip() or "running"))
        return
    if assessment.device.wsl:
        assessment.checks.append(Check("container runtime", False, "docker does not answer"))
        assessment.owner_actions.append(
            "OWNER ACTION REQUIRED: turn on Docker Desktop's WSL integration for this distribution, "
            "or start Docker Engine inside WSL (`sudo service docker start`)"
        )
        return
    if assessment.device.system == LINUX:
        assessment.checks.append(Check("container runtime", False, "docker does not answer"))
        assessment.owner_actions.append(
            "OWNER ACTION REQUIRED: start Docker Engine and allow your user to use it "
            "(`sudo systemctl enable --now docker` and `sudo usermod -aG docker $USER`, then log in again)"
        )
        return
    if _version(COLIMA) is None:
        if shutil.which("brew") is None:
            assessment.checks.append(Check("container runtime", False, "none running; Colima needs Homebrew"))
            return
        assessment.packages.append(COLIMA.brew)
    assessment.steps.append(Step("colima start", lambda: _run(["colima", "start"])))
    assessment.checks.append(Check("container runtime", False, "none running; pakem starts Colima"))


def _need_key(assessment: Assessment) -> None:
    private, public = device_key.private_path(), device_key.public_path()
    if private.exists() and public.exists():
        found = device_key.fingerprint(public)
        if found is None:
            raise PakemError(f"{display_path(public)} is not a valid SSH public key; fix or move it away")
        assessment.checks.append(Check("device key", True, f"{display_path(private)} {found}"))
        return
    if private.exists() or public.exists():
        raise CommandRefused(
            f"only one of {display_path(private)} and its .pub exists; move it away so a new pair can be made"
        )
    assessment.checks.append(Check("device key", False, f"missing: {display_path(private)}"))
    if not assessment.interactive:
        assessment.owner_actions.append(SETUP_KEY_ACTION)
        return
    assessment.creates_key = True
    comment = f"pakem-device {device_name()}"
    assessment.steps.append(
        Step(
            f"create {display_path(private)} (ssh-keygen asks for its passphrase)",
            lambda: device_key.create(comment, passphrase=True),
        )
    )


def _need_key_unlocked(assessment: Assessment) -> None:
    add = device_key.add_command(assessment.device.system == MACOS)
    if assessment.creates_key:
        assessment.steps.append(Step(f"{' '.join(add)} (asks for the passphrase once)", lambda: _run(add)))
        return
    private, public = device_key.private_path(), device_key.public_path()
    if not (private.exists() and public.exists()):
        return
    if not device_key.has_passphrase(private):
        assessment.checks.append(Check("device key unlocked", True, "no passphrase (chosen for this device)"))
        return
    state = device_key.agent_state(public)
    if state == device_key.IN_AGENT:
        assessment.checks.append(Check("device key unlocked", True, "held by ssh-agent"))
        return
    if state == device_key.NO_AGENT:
        assessment.checks.append(Check("device key unlocked", False, "no ssh-agent answers"))
        assessment.owner_actions.append(
            "OWNER ACTION REQUIRED: start an ssh-agent for your login (for example the desktop keyring, or "
            '`eval "$(ssh-agent -s)"` in your shell profile), then run `' + " ".join(add) + "`"
        )
        return
    assessment.checks.append(Check("device key unlocked", False, "not in ssh-agent"))
    if assessment.device.system == MACOS:
        assessment.steps.append(Step("load the key's passphrase from the Keychain", device_key.load_from_keychain))
    if assessment.interactive:
        assessment.steps.append(Step(f"{' '.join(add)} (asks for the passphrase)", lambda: _run(add)))
    else:
        assessment.owner_actions.append(f"OWNER ACTION REQUIRED: run `{' '.join(add)}` in your terminal")


def _need_login(assessment: Assessment, name: str) -> None:
    command, forge = LOGINS[name]
    if shutil.which(command) is None:
        return
    status = _probe((command, "auth", "status"))
    if status is not None and status.returncode == 0:
        assessment.checks.append(Check(f"{forge} login", True, f"{command} auth status: logged in"))
        return
    assessment.checks.append(Check(f"{forge} login", False, "not logged in"))
    assessment.owner_actions.append(f"OWNER ACTION REQUIRED: run `{command} auth login` in your terminal")


def _need_framework(assessment: Assessment, framework: Framework) -> None:
    installed = load_manifest().get("framework_version")
    if installed is not None:
        assessment.checks.append(Check("framework", True, f"Pakem {installed} installed"))
        return
    assessment.checks.append(Check("framework", False, "not installed"))
    command = [sys.executable, str(RUNNING_TOOL), "--root", str(framework.root), "install", "--apply"]
    assessment.steps.append(Step("pakem install --apply", lambda: _run(command)))


def device_settings_text(device: Device) -> str:
    public = device_key.public_path()
    present = public.exists() and device_key.private_path().exists()
    values = {
        "name": device_name(),
        "system": device.system,
        "wsl": device.wsl,
        "arch": device.arch,
        "key": display_path(device_key.private_path()),
        "key_fingerprint": (device_key.fingerprint(public) or "") if present else "",
        "key_passphrase": device_key.has_passphrase(device_key.private_path()) if present else True,
    }
    lines = ["# Written by pakem; device-local, never committed."]
    lines += [f"{key} = {json.dumps(value)}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def write_device_settings(device: Device) -> None:
    write_text_atomically(device_file(), device_settings_text(device))


def _need_settings(assessment: Assessment) -> None:
    """device.toml mirrors the device and its key; it is rewritten after any other step."""
    path = device_file()
    current = path.read_text(encoding="utf-8") if path.exists() else None
    up_to_date = current == device_settings_text(assessment.device)
    detail = display_path(path)
    if not up_to_date:
        detail += " missing or outdated"
    assessment.checks.append(Check("device settings", up_to_date, detail))
    if not up_to_date or assessment.steps:
        assessment.steps.append(Step(f"write {display_path(path)}", lambda: write_device_settings(assessment.device)))


def assess(framework: Framework, needs: tuple[str, ...], interactive: bool) -> Assessment:
    unknown = set(needs) - set(NEED_ORDER)
    if unknown:
        raise PakemError(f"internal error: unknown needs {sorted(unknown)}")
    assessment = Assessment(detect_device(), interactive)
    for name in NEED_ORDER:
        if name not in needs:
            continue
        if name in TOOLS:
            _need_tool(assessment, name)
        elif name == "container-runtime":
            _need_runtime(assessment)
        elif name == "device-key":
            _need_key(assessment)
        elif name == "device-key-unlocked":
            _need_key_unlocked(assessment)
        elif name in LOGINS:
            _need_login(assessment, name)
        elif name == "framework":
            _need_framework(assessment, framework)
        else:
            _need_settings(assessment)
    if assessment.packages:
        _plan_packages(assessment)
    elif any(not check.ok for check in assessment.checks) and shutil.which(_manager(assessment.device)) is None:
        assessment.owner_actions.append(_missing_manager(assessment.device))
    return assessment


def _plan_packages(assessment: Assessment) -> None:
    commands = _install_commands(assessment)
    description = " && ".join(" ".join(command) for command in commands)
    if assessment.device.system != MACOS and not _can_sudo(assessment):
        assessment.owner_actions.append(f"OWNER ACTION REQUIRED: run `{description}` in your terminal (needs sudo)")
        return

    def install() -> None:
        for command in commands:
            _run(command)

    assessment.steps.insert(0, Step(description, install))


def _missing_manager(device: Device) -> str:
    if device.system == MACOS:
        return "OWNER ACTION REQUIRED: install Homebrew (https://brew.sh), then run the command again"
    return "OWNER ACTION REQUIRED: this Linux has no apt; install the listed tools with its package manager"


def prepare(framework: Framework, needs: tuple[str, ...], args, command: str) -> tuple[Assessment, list[str]]:
    """Check, show the plan, confirm once, provide what is missing, and check again."""
    interactive = sys.stdin.isatty()
    assessment = assess(framework, needs, interactive)
    if not args.json:
        print(f"{command} ({assessment.device.label})")
        for step in assessment.steps:
            print(f"  {step.description}")
    if args.plan or not assessment.steps:
        return assessment, []
    confirm_steps(args.yes, command)
    done = []
    for step in assessment.steps:
        step.run()
        done.append(step.description)
    return assess(framework, needs, interactive), done


def ensure(framework: Framework, needs: tuple[str, ...], args, command: str) -> None:
    """Prepare a command's needs, then stop with whatever only the owner can still do."""
    assessment, _ = prepare(framework, needs, args, command)
    if args.plan:
        for action in assessment.owner_actions:
            print(action)
        return
    if assessment.owner_actions:
        raise OwnerActionRequired("\n".join(assessment.owner_actions))
    failed = [f"{check.name}: {check.detail}" for check in assessment.checks if not check.ok]
    if failed:
        raise CheckFailed(f"{command} needs: {'; '.join(failed)}")


def result(status: str, done: list[str], assessment: Assessment) -> dict:
    return {
        "status": status,
        "done": done,
        "owner_actions": assessment.owner_actions,
        "gaps": [],
        "checks": [check.as_dict() for check in assessment.checks],
    }


STATUS_NAMES = {EXIT_DONE: "done", EXIT_OWNER_ACTION: "owner-action", EXIT_CHECK_FAILED: "check-failed"}


def report(args, assessment: Assessment, done: list[str], title: str) -> int:
    code = assessment.outcome()
    if args.json:
        print(json.dumps(result(STATUS_NAMES[code], done, assessment), indent=2))
        return code
    print(title)
    for check in assessment.checks:
        print(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    for action in assessment.owner_actions:
        print(action)
    return code
