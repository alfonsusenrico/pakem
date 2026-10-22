"""`pakem setup device` and `pakem setup check`: the tools, key, and settings a device needs.

The device gets git, the forge CLIs (gh, glab), uv, and a working container runtime;
one dedicated SSH key that connects to hosts and signs releases (never the login key);
`~/.config/pakem/device.toml`; and the framework installed for its agent hosts.

Installs go through the system package manager after one confirmation: Homebrew on
macOS, apt on Debian and Ubuntu (with sudo, so the owner runs it). What the package
manager cannot provide, forge logins, and a missing package manager are reported as
owner actions with the exact instruction, never worked around (exit 3). Any running
container runtime is accepted; Colima is installed only when none answers.
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

from .common import (
    EXIT_CHECK_FAILED,
    EXIT_DONE,
    EXIT_OWNER_ACTION,
    CommandRefused,
    Framework,
    PakemError,
    confirm_steps,
    display_path,
    home_dir,
    utc_now_iso,
    write_text_atomically,
)
from .device_config import config_dir
from .manifest import load_manifest

MACOS = "macos"
LINUX = "linux"
KEY_NAME = "pakem_device_ed25519"
DEVICE_FILE = "device.toml"
PROBE_TIMEOUT_SECONDS = 20
UV_INSTRUCTIONS = "https://docs.astral.sh/uv/getting-started/installation/"
# The framework install runs as its own command, with the same tool that runs this setup.
RUNNING_TOOL = Path(__file__).resolve().parent.parent / "pakem"


@dataclass(frozen=True)
class Tool:
    name: str
    version_command: tuple[str, ...]
    brew: str | None
    apt: str | None
    manual: str
    systems: tuple[str, ...] = (MACOS, LINUX)


TOOLS = (
    Tool("git", ("git", "--version"), "git", "git", "https://git-scm.com/downloads"),
    Tool("gh", ("gh", "--version"), "gh", "gh", "https://github.com/cli/cli#installation"),
    Tool("glab", ("glab", "--version"), "glab", "glab", "https://gitlab.com/gitlab-org/cli#installation"),
    Tool("uv", ("uv", "--version"), "uv", None, UV_INSTRUCTIONS),
    Tool("docker", ("docker", "--version"), "docker", "docker.io", "https://docs.docker.com/engine/install/"),
)
COLIMA = Tool("colima", ("colima", "version"), "colima", None, "https://github.com/abiosoft/colima", (MACOS,))
FORGE_LOGINS = (("gh", "GitHub", "gh auth login"), ("glab", "GitLab", "glab auth login"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class DevicePlan:
    system: str
    arch: str
    checks: list[Check] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    owner_actions: list[str] = field(default_factory=list)
    start_colima: bool = False
    create_key: bool = False
    install_framework: bool = False


def detect_system() -> str:
    name = platform.system()
    if name == "Darwin":
        return MACOS
    if name == "Linux":
        return LINUX
    raise CommandRefused(f"unsupported system {name}; pakem setup supports macOS and Linux")


def key_path() -> Path:
    return home_dir() / ".ssh" / KEY_NAME


def device_file() -> Path:
    return config_dir() / DEVICE_FILE


def _probe(command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess | None:
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
    return lines[0] if lines else "installed"


def _apt_has(package: str) -> bool:
    completed = _probe(("apt-cache", "policy", package))
    if completed is None or completed.returncode != 0:
        return False
    return "Candidate:" in completed.stdout and "Candidate: (none)" not in completed.stdout


def _package_for(tool: Tool, system: str) -> str | None:
    if system == MACOS:
        return tool.brew if shutil.which("brew") else None
    if shutil.which("apt-get") is None or tool.apt is None:
        return None
    return tool.apt if _apt_has(tool.apt) else None


def _missing_package_manager(system: str) -> str:
    if system == MACOS:
        return "OWNER ACTION REQUIRED: install Homebrew (https://brew.sh), then run `pakem setup device` again"
    return "OWNER ACTION REQUIRED: this Linux has no apt; install the listed tools with its package manager"


def _plan_tools(plan: DevicePlan) -> None:
    for tool in TOOLS:
        version = _version(tool)
        if version is not None:
            plan.checks.append(Check(tool.name, True, version))
            continue
        package = _package_for(tool, plan.system)
        if package is not None:
            plan.packages.append(package)
            plan.checks.append(Check(tool.name, False, f"missing; `pakem setup device` installs package {package}"))
        else:
            plan.checks.append(Check(tool.name, False, "missing"))
            plan.owner_actions.append(f"OWNER ACTION REQUIRED: install {tool.name} ({tool.manual})")


def _plan_runtime(plan: DevicePlan) -> None:
    info = _probe(("docker", "info", "--format", "{{.OperatingSystem}}"))
    if info is not None and info.returncode == 0:
        plan.checks.append(Check("container runtime", True, info.stdout.strip() or "running"))
        return
    if plan.system == LINUX:
        plan.checks.append(Check("container runtime", False, "docker does not answer"))
        plan.owner_actions.append(
            "OWNER ACTION REQUIRED: start Docker Engine and allow your user to use it "
            "(`sudo systemctl enable --now docker` and `sudo usermod -aG docker $USER`, then log in again)"
        )
        return
    if _version(COLIMA) is None:
        if shutil.which("brew") is None:
            plan.checks.append(Check("container runtime", False, "none running; Colima needs Homebrew"))
            return
        plan.packages.append(COLIMA.brew)
    plan.start_colima = True
    plan.checks.append(Check("container runtime", False, "none running; `pakem setup device` starts Colima"))


def _fingerprint(public_key: Path) -> str | None:
    completed = _probe(("ssh-keygen", "-l", "-f", str(public_key)))
    if completed is None or completed.returncode != 0:
        return None
    return completed.stdout.split()[1] if len(completed.stdout.split()) > 1 else None


def _plan_key(plan: DevicePlan) -> None:
    private = key_path()
    public = private.with_name(private.name + ".pub")
    if private.exists() and public.exists():
        fingerprint = _fingerprint(public)
        if fingerprint is None:
            raise PakemError(f"{display_path(public)} is not a valid SSH public key; fix or move it away")
        plan.checks.append(Check("device key", True, f"{display_path(private)} {fingerprint}"))
        return
    if private.exists() or public.exists():
        raise CommandRefused(
            f"only one of {display_path(private)} and its .pub exists; move it away so a new pair can be made"
        )
    plan.create_key = True
    plan.checks.append(Check("device key", False, f"missing; `pakem setup device` creates {display_path(private)}"))


def _plan_logins(plan: DevicePlan) -> None:
    for command, forge, login in FORGE_LOGINS:
        if shutil.which(command) is None:
            continue
        status = _probe((command, "auth", "status"))
        if status is not None and status.returncode == 0:
            plan.checks.append(Check(f"{forge} login", True, f"{command} auth status: logged in"))
        else:
            plan.checks.append(Check(f"{forge} login", False, "not logged in"))
            plan.owner_actions.append(f"OWNER ACTION REQUIRED: run `{login}` in your terminal")


def build_plan() -> DevicePlan:
    plan = DevicePlan(detect_system(), platform.machine())
    _plan_tools(plan)
    manager = "brew" if plan.system == MACOS else "apt-get"
    if shutil.which(manager) is None and any(not check.ok for check in plan.checks):
        plan.owner_actions.append(_missing_package_manager(plan.system))
    _plan_runtime(plan)
    _plan_key(plan)
    installed = load_manifest().get("framework_version")
    plan.install_framework = installed is None
    if installed is None:
        plan.checks.append(Check("framework", False, "not installed; `pakem setup device` runs pakem install --apply"))
    else:
        plan.checks.append(Check("framework", True, f"Pakem {installed} installed"))
    settings_state = "present" if device_file().exists() else "missing"
    plan.checks.append(Check("device settings", device_file().exists(), f"{display_path(device_file())} {settings_state}"))
    _plan_logins(plan)
    if plan.packages:
        plan.steps.append(_install_description(plan))
    if plan.start_colima:
        plan.steps.append("colima start")
    if plan.create_key:
        plan.steps.append(f"ssh-keygen -t ed25519 -f {display_path(key_path())} (no passphrase; never the login key)")
    plan.steps.append(f"write {display_path(device_file())}")
    if plan.install_framework:
        plan.steps.append("pakem install --apply")
    return plan


def _install_command(plan: DevicePlan) -> list[list[str]]:
    if plan.system == MACOS:
        return [["brew", "install", *plan.packages]]
    return [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", *plan.packages]]


def _install_description(plan: DevicePlan) -> str:
    return " && ".join(" ".join(command) for command in _install_command(plan))


def _run(command: list[str]) -> None:
    # Installers ask for passwords and print progress, so they share this terminal.
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise PakemError(f"`{' '.join(command)}` failed with exit {completed.returncode}")


def _create_key() -> None:
    private = key_path()
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    comment = f"pakem-device {device_name()} {utc_now_iso()[:10]}"
    completed = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(private)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PakemError(f"ssh-keygen failed: {completed.stderr.strip()}")


def device_name() -> str:
    return socket.gethostname().split(".")[0]


def _write_device_file(plan: DevicePlan) -> None:
    public = key_path().with_name(KEY_NAME + ".pub")
    values = {
        "name": device_name(),
        "system": plan.system,
        "arch": plan.arch,
        "key": display_path(key_path()),
        "key_fingerprint": _fingerprint(public) or "",
        "updated_at": utc_now_iso(),
    }
    lines = ["# Written by `pakem setup device`; device-local, never committed."]
    lines += [f"{key} = {json.dumps(value)}" for key, value in values.items()]
    write_text_atomically(device_file(), "\n".join(lines) + "\n")




def _outcome(plan: DevicePlan) -> int:
    if plan.owner_actions:
        return EXIT_OWNER_ACTION
    if all(check.ok for check in plan.checks):
        return EXIT_DONE
    return EXIT_CHECK_FAILED


STATUS_NAMES = {EXIT_DONE: "done", EXIT_OWNER_ACTION: "owner-action", EXIT_CHECK_FAILED: "check-failed"}


def _result(status: str, done: list[str], plan: DevicePlan) -> dict:
    return {
        "status": status,
        "done": done,
        "owner_actions": plan.owner_actions,
        "gaps": [],
        "checks": [check.as_dict() for check in plan.checks],
    }


def _report(args, plan: DevicePlan, done: list[str], title: str) -> int:
    code = _outcome(plan)
    if args.json:
        print(json.dumps(_result(STATUS_NAMES[code], done, plan), indent=2))
        return code
    print(title)
    for check in plan.checks:
        print(f"  {'ok  ' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    for action in plan.owner_actions:
        print(action)
    return code


def run_setup_check(args) -> int:
    plan = build_plan()
    return _report(args, plan, [], f"pakem setup check ({plan.system}, {plan.arch})")


def run_setup_device(args) -> int:
    framework = Framework(Path(args.root))
    plan = build_plan()
    if not args.json:
        print(f"pakem setup device ({plan.system}, {plan.arch})")
        for step in plan.steps:
            print(f"  {step}")
    if args.plan:
        if args.json:
            print(json.dumps(_result("planned", plan.steps, plan), indent=2))
        else:
            print("Plan only: nothing changed.")
        return EXIT_DONE
    confirm_steps(args.yes, "pakem setup device")
    done: list[str] = []
    if plan.packages:
        for command in _install_command(plan):
            _run(command)
        done.append(_install_description(plan))
    if plan.start_colima:
        _run(["colima", "start"])
        done.append("colima start")
    if plan.create_key:
        _create_key()
        done.append(f"created {display_path(key_path())}")
    _write_device_file(plan)
    done.append(f"wrote {display_path(device_file())}")
    if plan.install_framework:
        _run([sys.executable, str(RUNNING_TOOL), "--root", str(framework.root), "install", "--apply"])
        done.append("pakem install --apply")
    after = build_plan()
    return _report(args, after, done, "Result:")
