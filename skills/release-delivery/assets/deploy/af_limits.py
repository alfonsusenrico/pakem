#!/usr/bin/env python3
"""Host limits measured at run time, or set by the owner for this host (ADR 0018).

    af_limits.py show                         every limit, its value, and where it came from
    af_limits.py runner-caps                  the CI runner slice: CPU quota, memory, runners per repo
    af_limits.py prune-runner --docker PATH   the CI runner's daily Docker clean-up (as its user)

Pakem ships rules, never numbers sized to one machine. Each limit is derived from what this
host has (its CPUs, its memory, the size and free space of the volume in question, the
measured size of recent builds) and recomputed on every run, so a bigger disk or a new
server changes it without an edit. A value the owner sets in the host settings file wins
over the derived one, and nothing else overrides it. The file is written by
`pakem setup host` from `pakem host limit`:

    [disk]
    reserve = "20GB"        # free space a build must leave (a size, or a share like "10%")
    build_cache = "8GB"     # each builder's cache, after every build and every daily clean-up
    alert_free = "30GB"     # the monitor alerts below this; it must stay above the reserve
    alert_hours = 72        # ... or when the trend reaches the reserve within this many hours

    [builder]               # the release receiver's BuildKit container
    cpus = 2
    memory = "4GB"          # or a share of the host's memory, like "30%"

    [runner]                # the CI runner's slice, shared by all its jobs
    cpus = 4
    memory = "6GB"
    per_repo = 8            # most runners one repository may register
    image_age_hours = 72    # unused images younger than this stay as cache

Standard library only. Readable by the deploy user, the monitor, and the runner's user:
it holds no secrets. Measured sizes are never printed with paths or project names.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_FILE = Path("/etc/pakem/host.toml")
STATE_DIR = Path("/var/lib/pakem")
BUILDS_FILE = "builds.json"

# The derivation rules (ADR 0018). Shares, not sizes: they scale with the host.
RESERVE_SHARE = 0.10        # free space a build must leave before any build was measured
RESERVE_FLOOR_SHARE = 0.05  # never less than this, so running services keep room to write
BUILD_MARGIN = 1.5          # the largest recent build, with room for it to grow
RECENT_BUILDS = 10
CACHE_SHARE = 0.10          # a build cache never exceeds this share of its volume ...
CACHE_HEADROOM_SHARE = 0.5  # ... nor half the space left above the reserve
ALERT_MARGIN = 1.5          # the alert level sits this far above the reserve
ALERT_HOURS = 72
BUILDER_CPU_SHARE = 0.5
BUILDER_MEMORY_SHARE = 0.30
RUNNER_CPU_SHARE = 0.5
RUNNER_MEMORY_SHARE = 0.40  # with the builder's 30%, production keeps at least 30%
RUNNERS_PER_CPU = 2         # check jobs often wait on the network and the disk
IMAGE_AGE_HOURS = 72
MAX_SHARE = 0.9

UNITS = {
    "": 1, "b": 1,
    "k": 1000, "kb": 1000, "m": 1000**2, "mb": 1000**2, "g": 1000**3, "gb": 1000**3, "t": 1000**4, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}
SIZE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)\s*")
SHARE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*")
SCHEMA = {
    "disk": {"reserve": "size", "build_cache": "size", "alert_free": "size", "alert_hours": "hours"},
    "builder": {"cpus": "cpus", "memory": "size"},
    "runner": {"cpus": "cpus", "memory": "size", "per_repo": "count", "image_age_hours": "hours"},
}
SET = "set in the host settings"
DERIVED = "derived"


class LimitError(Exception):
    """A settings file or a measurement this host cannot use."""


def parse_size(text: str | int) -> int:
    """Bytes from `12GB`, `9.5GiB`, `800M`, or a whole number of bytes. Decimal units are
    powers of 1000, as Docker prints them; binary units (KiB, MiB, GiB, TiB) are powers of 1024."""
    if isinstance(text, bool):
        raise LimitError(f"{text!r} is not a size")
    if isinstance(text, int):
        if text < 0:
            raise LimitError(f"{text} is not a size")
        return text
    match = SIZE.fullmatch(str(text))
    unit = match.group(2).lower() if match else None
    if match is None or unit not in UNITS:
        raise LimitError(f"{text!r} is not a size (for example 20GB, 512MiB)")
    return int(float(match.group(1)) * UNITS[unit])


def format_size(size: int) -> str:
    for unit, scale in (("TB", 1000**4), ("GB", 1000**3), ("MB", 1000**2), ("kB", 1000)):
        if size >= scale:
            return f"{size / scale:.1f}{unit}"
    return f"{size}B"


@dataclass(frozen=True)
class Amount:
    """An owner value: an absolute size, or a share of the whole it applies to."""

    size: int | None = None
    share: float | None = None

    @classmethod
    def parse(cls, value: object, key: str) -> Amount:
        if isinstance(value, str) and SHARE.fullmatch(value):
            share = float(SHARE.fullmatch(value).group(1)) / 100
            if not 0 < share <= MAX_SHARE:
                raise LimitError(f"{key} must be a share above 0% and at most {MAX_SHARE:.0%}")
            return cls(share=share)
        try:
            return cls(size=parse_size(value))  # type: ignore[arg-type]
        except LimitError as error:
            raise LimitError(f"{key}: {error}") from None

    def of(self, whole: int) -> int:
        return self.size if self.size is not None else int(whole * (self.share or 0))


@dataclass
class Settings:
    """The owner's values for this host. Absent keys are derived."""

    values: dict[str, dict[str, object]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> Settings:
        if not path.exists():
            return cls()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise LimitError(f"cannot read the host settings: {error}") from None
        return cls.parse(data)

    @classmethod
    def parse(cls, data: dict) -> Settings:
        values: dict[str, dict[str, object]] = {}
        for section, keys in data.items():
            if section not in SCHEMA or not isinstance(keys, dict):
                raise LimitError(f"host settings: unknown section [{section}]")
            for key, raw in keys.items():
                kind = SCHEMA[section].get(key)
                name = f"{section}.{key}"
                if kind is None:
                    raise LimitError(f"host settings: unknown key {name}")
                values.setdefault(section, {})[key] = cls._value(kind, raw, name)
        settings = cls(values)
        reserve, alert = settings.get("disk", "reserve"), settings.get("disk", "alert_free")
        if isinstance(reserve, Amount) and isinstance(alert, Amount) and reserve.share is None and alert.share is None:
            if alert.size <= reserve.size:  # type: ignore[operator]
                raise LimitError("host settings: disk.alert_free must be above disk.reserve")
        return settings

    @staticmethod
    def _value(kind: str, raw: object, name: str) -> object:
        if kind == "size":
            return Amount.parse(raw, name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise LimitError(f"host settings: {name} must be a number")
        if kind == "cpus" and raw > 0:
            return float(raw)
        if kind in ("count", "hours") and isinstance(raw, int) and raw >= 1:
            return raw
        raise LimitError(f"host settings: {name} must be {'above 0' if kind == 'cpus' else 'a whole number from 1'}")

    def get(self, section: str, key: str) -> object | None:
        return self.values.get(section, {}).get(key)


@dataclass(frozen=True)
class Limit:
    value: int | float
    source: str


@dataclass(frozen=True)
class Volume:
    total: int
    free: int


@dataclass(frozen=True)
class Host:
    """What this host has: CPUs this process may use and memory in bytes."""

    cpus: float
    memory: int

    @classmethod
    def measure(cls) -> Host:
        try:
            cpus = float(len(os.sched_getaffinity(0)))
        except AttributeError:
            cpus = float(os.cpu_count() or 1)
        return cls(cpus=cpus, memory=memory_total())


def memory_total(meminfo: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    raise LimitError("cannot read the host's memory size from /proc/meminfo")


def volume(path: Path, disk_usage=shutil.disk_usage) -> Volume:
    """The file system holding `path`, measured at its parent when this user may not enter it."""
    for candidate in (path, path.parent):
        try:
            usage = disk_usage(candidate)
            return Volume(total=usage.total, free=usage.free)
        except OSError:
            continue
    raise LimitError(f"cannot measure the free space at {path}")


# -- derivation ------------------------------------------------------------------------------


def reserve(disk: Volume, builds: list[int], settings: Settings) -> Limit:
    """Free space a build must leave on a volume. Derived: the largest recent build with a
    margin, never below a small share of the volume; before any build, a share of it."""
    owner = settings.get("disk", "reserve")
    if isinstance(owner, Amount):
        return Limit(owner.of(disk.total), SET)
    floor = int(disk.total * RESERVE_FLOOR_SHARE)
    if builds:
        return Limit(max(int(max(builds[-RECENT_BUILDS:]) * BUILD_MARGIN), floor),
                     f"{DERIVED} from the largest of the last {min(len(builds), RECENT_BUILDS)} builds")
    return Limit(int(disk.total * RESERVE_SHARE), f"{DERIVED} from the volume's size, before any build was measured")


def build_cache(disk: Volume, cache_used: int, kept: int, settings: Settings) -> Limit:
    """A builder's cache cap, recomputed on every run: a share of the volume, and at most half
    of what is left above the reserve once this cache is counted as free."""
    owner = settings.get("disk", "build_cache")
    if isinstance(owner, Amount):
        return Limit(owner.of(disk.total), SET)
    headroom = max(0, disk.free + cache_used - kept)
    return Limit(int(min(disk.total * CACHE_SHARE, headroom * CACHE_HEADROOM_SHARE)), DERIVED)


def alert_free(disk: Volume, kept: int, settings: Settings) -> Limit:
    """The monitor's low-space level, always above the reserve so it fires before builds are refused."""
    owner = settings.get("disk", "alert_free")
    if isinstance(owner, Amount):
        value = owner.of(disk.total)
        if value <= kept:
            raise LimitError(
                f"disk.alert_free ({format_size(value)}) is not above the reserve ({format_size(kept)}), "
                "so it would fire only after builds are refused"
            )
        return Limit(value, SET)
    return Limit(max(int(kept * ALERT_MARGIN), kept + 1), DERIVED)


def alert_hours(settings: Settings) -> Limit:
    owner = settings.get("disk", "alert_hours")
    return Limit(owner, SET) if isinstance(owner, int) else Limit(ALERT_HOURS, DERIVED)


def _cpus(host: Host, settings: Settings, section: str, share: float) -> Limit:
    owner = settings.get(section, "cpus")
    if isinstance(owner, float):
        return Limit(owner, SET)
    return Limit(max(1.0, math.floor(host.cpus * share * 10) / 10), DERIVED)


def _memory(host: Host, settings: Settings, section: str, share: float) -> Limit:
    owner = settings.get(section, "memory")
    if isinstance(owner, Amount):
        return Limit(owner.of(host.memory), SET)
    return Limit(int(host.memory * share), DERIVED)


def builder_cpus(host: Host, settings: Settings) -> Limit:
    return _cpus(host, settings, "builder", BUILDER_CPU_SHARE)


def builder_memory(host: Host, settings: Settings) -> Limit:
    return _memory(host, settings, "builder", BUILDER_MEMORY_SHARE)


def runner_cpus(host: Host, settings: Settings) -> Limit:
    return _cpus(host, settings, "runner", RUNNER_CPU_SHARE)


def runner_memory(host: Host, settings: Settings) -> Limit:
    return _memory(host, settings, "runner", RUNNER_MEMORY_SHARE)


def runners_per_repo(host: Host, settings: Settings) -> Limit:
    owner = settings.get("runner", "per_repo")
    if isinstance(owner, int):
        return Limit(owner, SET)
    return Limit(max(1, int(runner_cpus(host, settings).value * RUNNERS_PER_CPU)), DERIVED)


def image_age_hours(settings: Settings) -> Limit:
    owner = settings.get("runner", "image_age_hours")
    return Limit(owner, SET) if isinstance(owner, int) else Limit(IMAGE_AGE_HOURS, DERIVED)


def builder_options(host: Host, settings: Settings) -> list[str]:
    """`docker buildx create --driver-opt` values for the release builder's container."""
    cpus = builder_cpus(host, settings).value
    memory = builder_memory(host, settings).value
    return [f"memory={int(memory) // 1024**2}m", "cpu-period=100000", f"cpu-quota={int(cpus * 100000)}"]


def hours_until(samples: list[tuple[float, int]], level: int) -> float | None:
    """Hours until free space falls to `level` on the least-squares trend of (time, free)
    samples; None while the trend is flat or rising, or with too little history."""
    if len(samples) < 3 or samples[-1][0] - samples[0][0] < 3600:
        return None
    count = len(samples)
    mean_t = sum(t for t, _ in samples) / count
    mean_f = sum(f for _, f in samples) / count
    spread = sum((t - mean_t) ** 2 for t, _ in samples)
    slope = sum((t - mean_t) * (f - mean_f) for t, f in samples) / spread
    if slope >= 0:
        return None
    return max(0.0, (samples[-1][1] - level) / -slope / 3600)


# -- build history ------------------------------------------------------------------------


def load_builds(state_dir: Path = STATE_DIR) -> list[int]:
    try:
        data = json.loads((state_dir / BUILDS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sizes = [entry.get("bytes") for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []
    return [size for size in sizes if isinstance(size, int) and size >= 0]


def record_build(size: int, state_dir: Path = STATE_DIR, now: float | None = None) -> None:
    """Keep the measured disk use of the latest builds; the reserve follows them."""
    try:
        data = json.loads((state_dir / BUILDS_FILE).read_text(encoding="utf-8"))
        entries = [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        entries = []
    entries.append({"at": int(now if now is not None else time.time()), "bytes": int(size)})
    temporary = state_dir / f".{BUILDS_FILE}.tmp"
    temporary.write_text(json.dumps(entries[-RECENT_BUILDS:]) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(state_dir / BUILDS_FILE)


# -- Docker measurements --------------------------------------------------------------------


def total_line(output: str) -> int:
    """Bytes in `docker buildx du`'s `Total:` line; 0 for an empty or stopped builder."""
    for line in output.splitlines():
        if line.strip().startswith("Total:"):
            return parse_size(line.split(":", 1)[1].strip())
    return 0


def build_cache_line(output: str) -> int:
    """Bytes of the `Build Cache` row of `docker system df --format '{{.Type}}\\t{{.Size}}'`."""
    for line in output.splitlines():
        kind, _, size = line.partition("\t")
        if kind.strip() == "Build Cache":
            return parse_size(size.strip())
    return 0


# BuildKit names the size bound three ways across versions (2026-11-03): --max-used-space
# needs BuildKit v0.17 in the daemon, --reserved-space is the same keep-bytes limit under its
# buildx 0.17+ name, and --keep-storage the name older buildx knows (0.13 on Debian 13).
CACHE_LIMIT_FLAGS = ("--max-used-space", "--reserved-space", "--keep-storage")


def prune_cache(run, base: list[str], limit: int) -> str | None:
    """Prune a build cache down to `limit` bytes with the first flag this Docker accepts.
    Returns the flag that worked, or None when every one failed."""
    for flag in CACHE_LIMIT_FLAGS:
        if run([*base, "--force", flag, str(limit)]).returncode == 0:
            return flag
    return None


# -- commands -------------------------------------------------------------------------------


def docker_runner(docker: str):
    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([docker, *argv[1:]] if argv[0] == "docker" else argv,
                              capture_output=True, text=True, timeout=1800, check=False)
    return run


def docker_root(run) -> Path:
    info = run(["docker", "info", "--format", "{{.DockerRootDir}}"])
    root = info.stdout.strip()
    if info.returncode != 0 or not root.startswith("/"):
        raise LimitError("could not read Docker's data root (`docker info` failed)")
    return Path(root)


def prune_runner(docker: str, settings: Settings, run=None, disk_usage=shutil.disk_usage,
                 state_dir: Path = STATE_DIR) -> int:
    """The runner's daily clean-up: its build cache to the derived cap, unused images past
    their age, and every unused image when the volume is below the alert level."""
    run = run or docker_runner(docker)
    disk = volume(docker_root(run), disk_usage)
    used = build_cache_line(run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"]).stdout)
    kept = reserve(disk, load_builds(state_dir), settings).value
    cap = build_cache(disk, used, int(kept), settings)
    flag = prune_cache(run, ["docker", "builder", "prune"], int(cap.value))
    if flag is None:
        print("af-limits: the build cache clean-up failed with every size flag", file=sys.stderr)
        return 1
    print(f"af-limits: build cache kept under {format_size(int(cap.value))} ({cap.source}, {flag})")
    age = image_age_hours(settings)
    if run(["docker", "image", "prune", "--all", "--force", "--filter", f"until={age.value}h"]).returncode != 0:
        print("af-limits: the image clean-up failed", file=sys.stderr)
        return 1
    after = volume(docker_root(run), disk_usage)
    if after.free < alert_free(after, int(kept), settings).value:
        print("af-limits: free space is below the alert level: removing every unused image")
        if run(["docker", "image", "prune", "--all", "--force"]).returncode != 0:
            return 1
    print(f"af-limits: unused images older than {age.value} h removed ({age.source})")
    return 0


def show(settings: Settings, host: Host, disk: Volume | None, builds: list[int]) -> list[str]:
    rows = [
        ("builder cpus", builder_cpus(host, settings), str),
        ("builder memory", builder_memory(host, settings), format_size),
        ("runner cpus", runner_cpus(host, settings), str),
        ("runner memory", runner_memory(host, settings), format_size),
        ("runners per repository", runners_per_repo(host, settings), str),
        ("runner image age (hours)", image_age_hours(settings), str),
        ("disk alert trend (hours)", alert_hours(settings), str),
    ]
    if disk is not None:
        kept = reserve(disk, builds, settings)
        rows = [
            ("disk reserve", kept, format_size),
            ("disk alert level", alert_free(disk, int(kept.value), settings), format_size),
            ("build cache cap now", build_cache(disk, 0, int(kept.value), settings), format_size),
        ] + rows
    return [f"{name}: {render(limit.value)} ({limit.source})" for name, limit, render in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="af_limits.py", description="Host limits (ADR 0018)")
    parser.add_argument("--settings", type=Path, default=SETTINGS_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="every limit, its value, and where it came from")
    commands.add_parser("runner-caps", help="CPU quota, memory, and runners per repository for the runner slice")
    prune = commands.add_parser("prune-runner", help="the runner's daily Docker clean-up")
    prune.add_argument("--docker", default="docker")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(args.settings)
        if args.command == "prune-runner":
            return prune_runner(args.docker, settings)
        host = Host.measure()
        if args.command == "runner-caps":
            cpus = runner_cpus(host, settings).value
            memory = int(runner_memory(host, settings).value) // 1024**2
            print(f"{int(cpus * 100)}% {memory}M {runners_per_repo(host, settings).value}")
            return 0
        try:
            disk = volume(docker_root(docker_runner("docker")))
        except (LimitError, OSError):
            disk = None
        for line in show(settings, host, disk, load_builds()):
            print(line)
        return 0
    except LimitError as error:
        print(f"af-limits: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
