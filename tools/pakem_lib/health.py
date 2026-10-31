"""`pakem health`: the device's state and what it can safely shed (ADR 0016).

`check` is read-only: hardware and OS, battery and thermal state, disk, agent sessions and
builds, listening ports, Docker, and build output under the configured workspace roots.
Each reclaimable item is a finding with a stable id and a tier:

- zero: regenerates on the next build at no visible cost;
- warm: regenerable, but costly to rebuild or pull again.

Cold candidates (whole idle repositories, old toolchains) are the owner's to judge and
are never produced here. Named Docker volumes, `target/*/deps`, young `incremental`
folders, git trees, agent and toolchain folders, and `/tmp` are never findings.

`clean <id>...` deletes only findings of the latest check, after re-verifying each one at
that moment. Evidence for every probe and rule: the health agent's handoff of 2026-10-31,
checked on the owner's MacBook (ADR 0016).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    CommandRefused,
    PakemError,
    confirm_steps,
    display_path,
    expand_user_path,
    home_dir,
    utc_now_iso,
    write_text_atomically,
)
from .device_config import config_dir
from .manifest import state_dir

CONFIG_NAME = "health.toml"
LAST_CHECK_NAME = "health-last-check.json"
ZERO = "zero"
WARM = "warm"
GIB = 1024**3
DAY = 86_400
DEFAULTS = {
    "incremental_age_hours": 24,
    "cross_target_age_hours": 48,
    "build_cache_age_hours": 24,
    "critical_free_percent": 10,
    "critical_free_gib": 20,
    "healthy_free_percent": 15,
    "healthy_free_gib": 30,
    "scan_depth": 5,
    "last_check_max_age_hours": 24,
}
COMMAND_TIMEOUT = 30
ANONYMOUS_VOLUME = re.compile(r"^[0-9a-f]{64}$")
TARGET_TRIPLE = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+){2,3}$")
# Never descended into: version control, agent state, and folders the check has no business in.
SKIP_DIRS = {".git", ".hg", ".svn", ".claude", ".codex", ".gemini", ".agents", "__pycache__", ".cache",
             ".gradle", ".cargo", ".rustup"}
AGENT_MARKERS = ("/.local/bin/claude", "claude-code", "/codex", "codex ", "antigravity", "/agy", " agy ", "gemini")
COMPILERS = ("cargo", "rustc")
BUILDERS = ("cargo", "rustc", "node", "npm", "pnpm", "yarn", "next", "python", "uv", "pytest", "gradle", "java",
            "kotlin", "go", "make")
DAEMON_MARKERS = ("GradleDaemon", "KotlinCompileDaemon")


# ---------- configuration ----------

def config_path() -> Path:
    return config_dir() / CONFIG_NAME


def load_settings() -> tuple[list[Path], dict]:
    """Workspace roots and thresholds from ~/.config/pakem/health.toml (device-local)."""
    path = config_path()
    settings = dict(DEFAULTS)
    if not path.is_file():
        return [], settings
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc
    for key, value in data.items():
        if key == "roots":
            continue
        if key not in DEFAULTS:
            raise PakemError(f"{display_path(path)}: unknown setting {key!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise PakemError(f"{display_path(path)}: {key} must be a non-negative number")
        settings[key] = value
    roots = data.get("roots", [])
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise PakemError(f"{display_path(path)}: roots must be a list of folders")
    return [expand_user_path(root) for root in roots], settings


# ---------- small helpers ----------

def run(command: list[str], timeout: int = COMMAND_TIMEOUT, allow_nonzero: bool = False) -> str | None:
    """A read-only probe; None when the program is missing, fails, or times out."""
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                   env=dict(os.environ, LANG="C", LC_ALL="C"))
    except (subprocess.TimeoutExpired, OSError):
        return None
    return completed.stdout if (completed.returncode == 0 or allow_nonzero) else None


def allocated(path: Path, seen: set[tuple[int, int]] | None = None) -> int:
    """Bytes a file or folder occupies on disk: allocated blocks, each hard link once.
    st_size misleads for sparse files (a 228 GiB Docker.raw occupied 21 GiB)."""
    seen = set() if seen is None else seen
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            info = current.lstat()
        except OSError:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            continue
        seen.add(key)
        total += getattr(info, "st_blocks", 0) * 512
        if current.is_dir() and not current.is_symlink():
            try:
                stack.extend(Path(entry.path) for entry in os.scandir(current))
            except OSError:
                continue
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1000
    return f"{size} B"


def parse_size(text: str) -> int | None:
    """Docker and Homebrew sizes such as '4.14GB', '332.4MB', or '0B'."""
    match = re.match(r"^\s*([\d.]+)\s*([kKMGT]?i?B)", text)
    if not match:
        return None
    units = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
             "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    unit = match.group(2)
    return round(float(match.group(1)) * units[unit]) if unit in units else None


def finding_id(kind: str, target: str) -> str:
    return hashlib.sha256(f"{kind}:{target}".encode()).hexdigest()[:8]


def inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


# ---------- processes ----------

@dataclass
class Process:
    pid: int
    command: str
    cwd: str | None
    rss_kb: int = 0


def list_processes() -> list[Process]:
    """Every process with its command line, and the working folder where the platform allows."""
    if Path("/proc/self/cwd").exists():
        return _linux_processes()
    return _mac_processes()


def _linux_processes() -> list[Process]:
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            cwd = os.readlink(entry / "cwd")
            rss = 0
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
        except (OSError, ValueError):
            continue
        if command:
            processes.append(Process(int(entry.name), command, cwd, rss))
    return processes


def _mac_processes() -> list[Process]:
    listing = run(["ps", "-axo", "pid=,rss=,args="]) or ""
    processes = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            processes.append(Process(int(parts[0]), parts[2], None, int(parts[1]) if parts[1].isdigit() else 0))
    wanted = [process for process in processes if is_agent(process) or is_builder(process)]
    if wanted:
        # -a joins the conditions: without it lsof scans every descriptor of every process.
        # lsof exits 1 when any queried pid has exited or belongs to another user, while still
        # printing the readable pids to stdout.
        output = run(["lsof", "-a", "-p", ",".join(str(process.pid) for process in wanted), "-d", "cwd", "-Fpn"],
                     allow_nonzero=True) or ""
        cwd_by_pid: dict[int, str] = {}
        pid = None
        for line in output.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and pid is not None:
                cwd_by_pid[pid] = line[1:]
        for process in processes:
            process.cwd = cwd_by_pid.get(process.pid)
    return processes


def is_agent(process: Process) -> bool:
    lowered = process.command.lower()
    if "app-server" in lowered or "code-mode-host" in lowered:
        return False
    return any(marker in lowered for marker in AGENT_MARKERS)


def is_compiler(process: Process) -> bool:
    name = Path(process.command.split()[0]).name if process.command else ""
    return name in COMPILERS


def is_builder(process: Process) -> bool:
    name = Path(process.command.split()[0]).name.lower() if process.command else ""
    return name in BUILDERS or name.startswith(("python3", "pytest-"))


def _worktree(project: Path) -> Path:
    """The enclosing git worktree when one exists, so a session at the repo root protects
    build folders in subpackages (`frontend/node_modules`, `backend/.venv`)."""
    for candidate in (project, *project.parents):
        if (candidate / ".git").exists():
            return candidate
    return project


def active_in(project: Path, processes: list[Process], test) -> bool:
    scope = _worktree(project)
    return any(process.cwd and test(process) and inside(Path(process.cwd), scope) for process in processes)


# ---------- findings ----------

@dataclass
class Finding:
    id: str
    kind: str
    tier: str
    title: str
    size: int
    age_days: float | None
    in_use: bool
    paths: list[str] = field(default_factory=list)
    project: str | None = None
    docker: str | None = None
    note: str | None = None


def _age_days(path: Path) -> float | None:
    try:
        return (time.time() - path.stat().st_mtime) / DAY
    except OSError:
        return None


def _project_dirs(roots: list[Path], depth: int):
    """(project, build folder name, build folder) for every known build output under the roots."""
    markers = {"target": ("Cargo.toml",), "node_modules": ("package.json",), ".next": ("package.json",),
               ".venv": ("pyproject.toml", "requirements.txt", "uv.lock", "setup.py")}
    stack = [(root, 0) for root in roots if root.is_dir()]
    while stack:
        folder, level = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            if name in markers:
                if any((folder / marker).exists() for marker in markers[name]):
                    yield folder, name, Path(entry.path)
                continue
            if name in SKIP_DIRS or (name.startswith(".") and name not in markers):
                continue
            if level + 1 < depth:
                stack.append((Path(entry.path), level + 1))


def build_findings(roots: list[Path], settings: dict, processes: list[Process]) -> list[Finding]:
    findings: list[Finding] = []
    incremental_age = settings["incremental_age_hours"] / 24
    cross_age = settings["cross_target_age_hours"] / 24
    for project, name, folder in _project_dirs(roots, int(settings["scan_depth"])):
        project_active = active_in(project, processes, lambda process: is_agent(process) or is_builder(process))
        compiling = active_in(project, processes, is_compiler)
        if name == "target":
            # incremental sits at target/<profile>/incremental or target/<triple>/<profile>/incremental:
            # looking only there avoids walking a target folder of 180,000 files.
            for incremental in sorted([*folder.glob("*/incremental"), *folder.glob("*/*/incremental")]):
                if not incremental.is_dir() or incremental.is_symlink():
                    continue
                stale = [Path(entry.path) for entry in os.scandir(incremental)
                         if entry.is_dir(follow_symlinks=False) and (_age_days(Path(entry.path)) or 0) > incremental_age]
                if stale:
                    seen: set = set()
                    size = sum(allocated(path, seen) for path in stale)
                    target = str(incremental)
                    findings.append(Finding(
                        finding_id("rust-incremental", target), "rust-incremental", ZERO,
                        f"{len(stale)} stale incremental folders (> {settings['incremental_age_hours']:g}h)",
                        size, None, compiling, [str(path) for path in stale], str(project),
                        note="cargo or rustc is running in this project" if compiling else None))
            for entry in os.scandir(folder):
                path = Path(entry.path)
                age = _age_days(path)
                if (entry.is_dir(follow_symlinks=False) and TARGET_TRIPLE.match(entry.name)
                        and age is not None and age > cross_age):
                    findings.append(Finding(
                        finding_id("rust-cross-target", str(path)), "rust-cross-target", WARM,
                        f"idle cross-compile output {entry.name}", allocated(path), age,
                        compiling, [str(path)], str(project)))
            continue
        findings.append(Finding(
            finding_id(f"build-{name}", str(folder)), f"build-{name}", WARM, f"{name} of {project.name}",
            allocated(folder), _age_days(folder), project_active, [str(folder)], str(project),
            note="an agent or build runs in this project" if project_active else None))
    return findings


# ---------- Docker and Homebrew ----------

@dataclass
class DockerState:
    available: bool = False
    containers: list[dict] = field(default_factory=list)
    named_volumes: list[str] = field(default_factory=list)
    anonymous_volumes: list[str] = field(default_factory=list)
    mounted: set[str] = field(default_factory=set)
    images: list[dict] = field(default_factory=list)
    used_images: set[str] = field(default_factory=set)
    build_cache_reclaimable: int | None = None
    volume_sizes: dict[str, int] = field(default_factory=dict)
    disk_image_allocated: int | None = None
    disk_image_apparent: int | None = None


def _docker_time(text: str) -> float | None:
    """Docker's '2026-10-21 08:33:17.762954466 +0000 UTC' as epoch seconds."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def docker_state(settings: dict) -> DockerState:
    state = DockerState()
    if run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15) is None:
        return state
    state.available = True
    ids = (run(["docker", "ps", "-aq", "--no-trunc"]) or "").split()
    if ids:
        details = json.loads(run(["docker", "inspect", *ids]) or "[]")
        for container in details:
            mounts = [mount.get("Name") for mount in container.get("Mounts", []) if mount.get("Type") == "volume"]
            state.mounted.update(name for name in mounts if name)
            state.used_images.add(container.get("Image", ""))
            state.containers.append({"name": container.get("Name", "").lstrip("/"),
                                     "running": bool(container.get("State", {}).get("Running")),
                                     "volumes": mounts})
    verbose = run(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=60)
    usage = json.loads(verbose) if verbose else {}
    for volume in usage.get("Volumes") or []:
        name = volume.get("Name", "")
        state.volume_sizes[name] = parse_size(volume.get("Size", "")) or 0
        (state.anonymous_volumes if ANONYMOUS_VOLUME.match(name) else state.named_volumes).append(name)
    for image in usage.get("Images") or []:
        state.images.append({"id": image.get("ID", ""), "name": f"{image.get('Repository')}:{image.get('Tag')}",
                             "size": parse_size(image.get("Size", "")) or 0,
                             "dangling": image.get("Repository") == "<none>"})
    cutoff = time.time() - settings["build_cache_age_hours"] * 3600
    for entry in usage.get("BuildCache") or []:
        last_used = _docker_time(entry.get("LastUsedAt") or entry.get("CreatedAt") or "")
        if str(entry.get("InUse")).lower() != "true" and last_used is not None and last_used < cutoff:
            state.build_cache_reclaimable = (state.build_cache_reclaimable or 0) + (parse_size(entry.get("Size", "")) or 0)
    disk_image = home_dir() / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    if disk_image.is_file():
        info = disk_image.stat()
        state.disk_image_allocated, state.disk_image_apparent = info.st_blocks * 512, info.st_size
    return state


def docker_findings(state: DockerState, settings: dict) -> list[Finding]:
    findings: list[Finding] = []
    if not state.available:
        return findings
    # One finding for all of them: test containers leave dozens, and clean re-checks each.
    unused = sorted(volume for volume in state.anonymous_volumes if volume not in state.mounted)
    if unused:
        findings.append(Finding(finding_id("docker-volumes", ",".join(unused)), "docker-volumes", WARM,
                                f"{len(unused)} anonymous volumes no container uses",
                                sum(state.volume_sizes.get(volume, 0) for volume in unused), None, False,
                                paths=unused, docker="volumes"))
    for image in state.images:
        if image["id"] in state.used_images:
            continue
        kind, tier = ("docker-image-dangling", ZERO) if image["dangling"] else ("docker-image", WARM)
        title = "untagged image" if image["dangling"] else f"image {image['name']} (no container)"
        findings.append(Finding(finding_id(kind, image["id"]), kind, tier, title, image["size"] or 0, None, False,
                                docker=image["id"]))
    if state.build_cache_reclaimable:
        findings.append(Finding(finding_id("docker-build-cache", "build-cache"), "docker-build-cache", ZERO,
                                f"Docker build cache older than {settings['build_cache_age_hours']:g}h",
                                state.build_cache_reclaimable, None, False, docker="build-cache"))
    return findings


def brew_finding() -> Finding | None:
    output = run(["brew", "cleanup", "-n"], timeout=60)
    if not output:
        return None
    match = re.search(r"free approximately ([\d.]+\s*[KMGT]?B)", output)
    size = parse_size(match.group(1).replace(" ", "")) if match else None
    if not size:
        return None
    return Finding(finding_id("brew-cleanup", "brew"), "brew-cleanup", ZERO, "Homebrew old versions and downloads",
                   size, None, False, note="runs `brew cleanup`")


# ---------- system facts ----------

def data_volume() -> Path:
    return Path("/System/Volumes/Data") if Path("/System/Volumes/Data").is_dir() else Path("/")


def disk_facts(settings: dict) -> dict:
    volume = data_volume()
    info = os.statvfs(volume)
    total, free = info.f_blocks * info.f_frsize, info.f_bavail * info.f_frsize
    percent = 100 * free / total if total else 0
    if percent < settings["critical_free_percent"] or free < settings["critical_free_gib"] * GIB:
        urgency = "critical"
    elif percent > settings["healthy_free_percent"] and free > settings["healthy_free_gib"] * GIB:
        urgency = "healthy"
    else:
        urgency = "watch"
    facts = {"volume": str(volume), "total": total, "free": free, "free_percent": round(percent, 1), "urgency": urgency}
    raw = run(["diskutil", "apfs", "list", "-plist"]) if sys.platform == "darwin" else None
    if raw:
        try:
            containers = plistlib.loads(raw.encode()).get("Containers", [])
        except plistlib.InvalidFileException:
            containers = []
        for container in containers:
            if any(volume_info.get("Roles") == ["Data"] for volume_info in container.get("Volumes", [])):
                facts["container"] = {
                    "ceiling": container.get("CapacityCeiling"),
                    "free": container.get("CapacityFree"),
                    "volumes": {", ".join(volume_info.get("Roles") or ["None"]): volume_info.get("CapacityInUse")
                                for volume_info in container.get("Volumes", [])},
                }
    return facts


def system_facts() -> dict:
    facts: dict = {"system": sys.platform, "machine": os.uname().machine, "load": [round(x, 2) for x in os.getloadavg()]}
    if sys.platform == "darwin":
        profile = run(["system_profiler", "SPHardwareDataType", "SPSoftwareDataType", "SPPowerDataType", "-json"], 60)
        data = json.loads(profile) if profile else {}
        hardware = (data.get("SPHardwareDataType") or [{}])[0]
        software = (data.get("SPSoftwareDataType") or [{}])[0]
        facts.update(model=hardware.get("machine_name"), chip=hardware.get("chip_type"),
                     memory=hardware.get("physical_memory"), os=software.get("os_version"),
                     uptime=software.get("uptime"))
        for entry in data.get("SPPowerDataType") or []:
            battery = entry.get("sppower_battery_health_info")
            if battery:
                facts["battery"] = {"cycles": battery.get("sppower_battery_cycle_count"),
                                    "condition": battery.get("sppower_battery_health"),
                                    "maximum_capacity": battery.get("sppower_battery_health_maximum_capacity")}
            charge = entry.get("sppower_battery_charge_info")
            if charge:
                facts.setdefault("battery", {})["charge_percent"] = charge.get("sppower_battery_state_of_charge")
        thermal = run(["pmset", "-g", "therm"]) or ""
        facts["thermal"] = "no warning" if "No thermal warning" in thermal else (thermal.strip() or "unknown")
        swap = run(["sysctl", "-n", "vm.swapusage"]) or ""
        used, total = re.search(r"used = ([\d.]+)M", swap), re.search(r"total = ([\d.]+)M", swap)
        facts["swap"] = (f"{human(int(float(used.group(1)) * 1024**2))} used of {human(int(float(total.group(1)) * 1024**2))}"
                         if used and total else "unknown")
        pressure = run(["memory_pressure"], 15) or ""
        match = re.search(r"free percentage:\s*(\d+)%", pressure)
        facts["memory_free_percent"] = int(match.group(1)) if match else None
    else:
        meminfo = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                meminfo[key] = int(value.split()[0]) * 1024
        except OSError:
            pass
        if meminfo.get("MemTotal"):
            facts["memory"] = human(meminfo["MemTotal"])
            facts["memory_free_percent"] = round(100 * meminfo.get("MemAvailable", 0) / meminfo["MemTotal"])
            facts["swap"] = f"{human(meminfo.get('SwapTotal', 0) - meminfo.get('SwapFree', 0))} used"
        try:
            pretty = [line for line in Path("/etc/os-release").read_text().splitlines() if line.startswith("PRETTY_NAME=")]
            facts["os"] = pretty[0].split("=", 1)[1].strip('"') if pretty else None
        except OSError:
            facts["os"] = None
        facts["wsl"] = "microsoft" in Path("/proc/version").read_text().lower() if Path("/proc/version").exists() else False
        batteries = sorted(Path("/sys/class/power_supply").glob("BAT*")) if Path("/sys/class/power_supply").is_dir() else []
        if batteries:
            def read(name: str) -> str | None:
                try:
                    return (batteries[0] / name).read_text().strip()
                except OSError:
                    return None
            facts["battery"] = {"charge_percent": read("capacity"), "status": read("status"),
                                "cycles": read("cycle_count")}
        temperatures = []
        for zone in Path("/sys/class/thermal").glob("thermal_zone*/temp") if Path("/sys/class/thermal").is_dir() else []:
            try:
                temperatures.append(int(zone.read_text().strip()) / 1000)
            except (OSError, ValueError):
                continue
        facts["thermal"] = f"hottest zone {max(temperatures):.0f} °C" if temperatures else "unknown"
    return facts


def listening_ports() -> list[dict]:
    ports = []
    if sys.platform == "darwin":
        output = run(["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n", "-Fpcn"]) or ""
        pid = command = None
        for line in output.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("c"):
                command = line[1:]
            elif line.startswith("n"):
                ports.append({"pid": pid, "command": command, "address": line[1:]})
    else:
        output = run(["ss", "-ltnpH"]) or ""
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                ports.append({"pid": None, "command": " ".join(parts[5:]) or None, "address": parts[3]})
    unique = {(port["address"], port["pid"]): port for port in ports}
    return sorted(unique.values(), key=lambda port: port["address"])


# ---------- the check ----------

def collect(full: bool = True) -> dict:
    """Everything the check reports. `full=False` skips Docker, Homebrew, system profile, and ports
    (the weekly report's fast summary)."""
    roots, settings = load_settings()
    started = time.time()
    processes = list_processes()
    findings = build_findings(roots, settings, processes)
    result: dict = {"generated": utc_now_iso(), "roots": [str(root) for root in roots],
                    "disk": disk_facts(settings)}
    if full:
        docker = docker_state(settings)
        findings += docker_findings(docker, settings)
        brew = brew_finding()
        if brew:
            findings.append(brew)
        result["system"] = system_facts()
        result["ports"] = listening_ports()
        result["docker"] = {
            "available": docker.available,
            "containers": docker.containers,
            "named_volumes_protected": docker.named_volumes,
            "anonymous_volumes": len(docker.anonymous_volumes),
            "images": len(docker.images),
            "disk_image": ({"allocated": docker.disk_image_allocated, "apparent": docker.disk_image_apparent}
                           if docker.disk_image_allocated is not None else None),
        }
    result["sessions"] = [{"pid": process.pid, "cwd": process.cwd, "command": process.command[:120]}
                          for process in processes if is_agent(process)]
    result["builds"] = [{"pid": process.pid, "cwd": process.cwd, "command": process.command[:120]}
                        for process in processes if is_builder(process) and process.cwd]
    result["daemons"] = [{"pid": process.pid, "rss": process.rss_kb * 1024,
                          "name": next(marker for marker in DAEMON_MARKERS if marker in process.command)}
                         for process in processes if any(marker in process.command for marker in DAEMON_MARKERS)]
    result["findings"] = [asdict(finding) for finding in sorted(findings, key=lambda item: (item.tier != ZERO, -item.size))]
    result["seconds"] = round(time.time() - started, 1)
    return result


def tier_totals(findings: list[dict]) -> dict[str, int]:
    totals = {ZERO: 0, WARM: 0}
    for finding in findings:
        if not finding["in_use"]:
            totals[finding["tier"]] += finding["size"]
    return totals


def render(result: dict) -> list[str]:
    lines = []
    system = result.get("system", {})
    if system:
        described = " · ".join(str(value) for value in (system.get("model"), system.get("chip"), system.get("memory"),
                                                         system.get("os")) if value)
        lines.append(f"Device: {described or system.get('system')} · load {' '.join(map(str, system['load']))}")
        battery = system.get("battery") or {}
        battery_text = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in battery.items() if value) or "none"
        lines.append(f"Battery: {battery_text} · Thermal: {system.get('thermal')} · "
                     f"Memory free: {system.get('memory_free_percent', 'unknown')}% · Swap: {system.get('swap')}")
    disk = result["disk"]
    lines.append(f"Disk ({disk['volume']}): {human(disk['free'])} free of {human(disk['total'])} "
                 f"({disk['free_percent']}% free) · {disk['urgency'].upper()}")
    lines.append(f"Agent sessions: {len(result['sessions'])} · builds running: {len(result['builds'])}")
    for daemon in result.get("daemons", []):
        lines.append(f"  idle-able daemon {daemon['name']} (pid {daemon['pid']}) holds {human(daemon['rss'])} of memory")
    docker = result.get("docker")
    if docker and docker["available"]:
        running = sum(1 for container in docker["containers"] if container["running"])
        count = len(docker["containers"])
        lines.append(f"Docker: {count} container{'s' if count != 1 else ''} ({running} running), "
                     f"{len(docker['named_volumes_protected'])} named volumes (protected), "
                     f"{docker['anonymous_volumes']} anonymous volumes, {docker['images']} images")
        if docker.get("disk_image"):
            lines.append(f"  Docker disk image occupies {human(docker['disk_image']['allocated'])} "
                         f"(reports {human(docker['disk_image']['apparent'])})")
    elif docker is not None:
        lines.append("Docker: not running")
    if "ports" in result:
        lines.append(f"Listening ports: {len(result['ports'])}")
    if not result["roots"]:
        lines.append(f"Build output: not scanned. Set `roots` in {display_path(config_path())}")
    totals = tier_totals(result["findings"])
    lines.append(f"Reclaimable now: zero-impact {human(totals[ZERO])}, warm {human(totals[WARM])}")
    for tier, label in ((ZERO, "Zero-impact"), (WARM, "Warm")):
        tier_findings = [finding for finding in result["findings"] if finding["tier"] == tier]
        if not tier_findings:
            continue
        lines.append(f"{label}:")
        for finding in tier_findings:
            where = display_path(Path(finding["project"])) if finding["project"] else ""
            state = " IN USE" if finding["in_use"] else ""
            age = f", {finding['age_days']:.0f}d idle" if finding.get("age_days") else ""
            lines.append(f"  [{finding['id']}] {human(finding['size']):>9}  {finding['title']}"
                         f"{(' · ' + where) if where else ''}{age}{state}")
    lines.append(f"Checked in {result['seconds']}s. Clean approved findings with `pakem health clean <id>...`.")
    return lines


def last_check_path() -> Path:
    return state_dir() / LAST_CHECK_NAME


def run_check(args) -> int:
    result = collect(full=True)
    write_text_atomically(last_check_path(), json.dumps(result, indent=2) + "\n")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for line in render(result):
            print(line)
    return 0


# ---------- clean ----------

def _verify(finding: dict, roots: list[Path], processes: list[Process], docker: DockerState | None) -> str | None:
    """Why the finding must not be deleted now, or None when it may be."""
    kind = finding["kind"]
    if kind.startswith(("rust-", "build-")):
        if not roots:
            return "no workspace roots are configured"
        project = Path(finding["project"])
        for raw in finding["paths"]:
            path = Path(raw)
            if path.is_symlink() or not path.is_dir():
                continue
            if not any(inside(path, root) for root in roots) or not inside(path, project):
                return f"{display_path(path)} is outside the workspace roots"
            if kind == "rust-incremental" and path.parent.name != "incremental":
                return f"{display_path(path)} is not an incremental folder"
            if kind.startswith("build-") and path.name != kind.removeprefix("build-"):
                return f"{display_path(path)} is not a {kind.removeprefix('build-')} folder"
        if kind.startswith("rust-") and active_in(project, processes, is_compiler):
            return "cargo or rustc is running in this project"
        if kind.startswith("build-") and active_in(project, processes, lambda p: is_agent(p) or is_builder(p)):
            return "an agent or build runs in this project"
        return None
    if docker is None or not docker.available:
        return "Docker is not running"
    if kind == "docker-volumes":
        if not all(ANONYMOUS_VOLUME.match(volume) for volume in finding["paths"]):
            return "only anonymous volumes are ever removed"
    if kind.startswith("docker-image") and finding["docker"] in docker.used_images:
        return "a container now uses this image"
    return None


def _remove(finding: dict, settings: dict) -> str | None:
    kind = finding["kind"]
    if kind.startswith(("rust-", "build-")):
        failures = []
        for raw in finding["paths"]:
            path = Path(raw)
            if path.is_dir() and not path.is_symlink():
                # Folders are removed directly, never with `cargo clean`: its lock on
                # target/.cargo-lock would stall a live session's build.
                shutil.rmtree(path, onerror=lambda _func, failed, _exc: failures.append(failed))
        return f"could not remove {len(failures)} entries" if failures else None
    if kind == "docker-volumes":
        # Each volume is checked against a fresh container list: test containers come and
        # go within minutes, and Docker refuses to remove a volume a container still uses.
        docker = docker_state(settings)
        failed = [volume for volume in finding["paths"]
                  if volume in docker.mounted or run(["docker", "volume", "rm", volume]) is None]
        return f"{len(failed)} of {len(finding['paths'])} volumes kept (in use or refused)" if failed else None
    commands = {
        "docker-image": ["docker", "image", "rm", finding.get("docker") or ""],
        "docker-image-dangling": ["docker", "image", "rm", finding.get("docker") or ""],
        "docker-build-cache": ["docker", "builder", "prune", "-f", "--filter",
                               f"until={settings['build_cache_age_hours']:g}h"],
        "brew-cleanup": ["brew", "cleanup"],
    }
    command = commands.get(kind)
    if command is None:
        return f"unknown finding kind {kind!r}"
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip().splitlines()
        return output[-1] if output else f"exit {completed.returncode}"
    return None


def run_clean(args) -> int:
    roots, settings = load_settings()
    path = last_check_path()
    if not path.is_file():
        raise CommandRefused("no health check yet. Run `pakem health check` first")
    last = json.loads(path.read_text(encoding="utf-8"))
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > settings["last_check_max_age_hours"]:
        raise CommandRefused(f"the last check is {age_hours:.0f}h old. Run `pakem health check` again")
    by_id = {finding["id"]: finding for finding in last.get("findings", [])}
    unknown = [finding_id_ for finding_id_ in args.ids if finding_id_ not in by_id]
    if unknown:
        raise CommandRefused(f"not in the last check: {', '.join(unknown)}. Run `pakem health check` again")
    chosen = [by_id[finding_id_] for finding_id_ in dict.fromkeys(args.ids)]
    print("pakem health clean:")
    for finding in chosen:
        print(f"  [{finding['id']}] {human(finding['size'])}  {finding['title']}")
    if args.plan:
        print("Plan only: nothing removed. Each finding is re-verified when it runs.")
        return 0
    confirm_steps(args.yes, "pakem health clean")
    processes = list_processes()
    docker = docker_state(settings) if any(finding["kind"].startswith("docker") for finding in chosen) else None
    freed, skipped = 0, []
    for finding in chosen:
        reason = _verify(finding, roots, processes, docker)
        if reason is None:
            reason = _remove(finding, settings)
        if reason:
            skipped.append(f"[{finding['id']}] {finding['title']}: {reason}")
            continue
        freed += finding["size"]
        print(f"  removed [{finding['id']}] {finding['title']}")
    for line in skipped:
        print(f"  skipped {line}")
    print(f"Freed about {human(freed)}. {len(skipped)} skipped.")
    return 0


# ---------- the weekly report's summary ----------

def summary_lines() -> list[str]:
    """Fast and read-only: disk urgency and build output, for the weekly drift report."""
    result = collect(full=False)
    disk = result["disk"]
    totals = tier_totals(result["findings"])
    lines = [f"Disk {human(disk['free'])} free ({disk['free_percent']}%), {disk['urgency']}"]
    if result["roots"]:
        lines.append(f"Build output reclaimable: zero-impact {human(totals[ZERO])}, warm {human(totals[WARM])}")
    lines.append("Details and Docker: `pakem health check`")
    return lines


def add_parser(commands) -> None:
    health = commands.add_parser("health", help="this device's health, and safe cleanup of build output and caches")
    sub = health.add_subparsers(dest="health_command", required=True)
    check = sub.add_parser("check", help="read-only: system, disk, sessions, Docker, build output, with findings")
    check.add_argument("--json", action="store_true", help="machine-readable result")
    clean = sub.add_parser("clean", help="remove findings of the last check by id, re-verified first")
    clean.add_argument("ids", nargs="+", help="finding ids from `pakem health check`")
    clean.add_argument("--plan", action="store_true", help="list what would be removed. Remove nothing")
    clean.add_argument("--yes", action="store_true", help="remove without asking (after the owner approved the ids)")


def run_health(args) -> int:
    return run_check(args) if args.health_command == "check" else run_clean(args)
