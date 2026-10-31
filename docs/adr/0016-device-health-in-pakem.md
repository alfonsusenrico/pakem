# 0016. Device health in `pakem`

- Status: Proposed (plan approved by the owner, 2026-10-31)
- Date: 2026-10-31

## Context

The owner ran device health audits and cleanups with a separate agent session on the
owner's MacBook and recovered about 22 GiB (stale Rust `target/debug/incremental` caches, idle
Android build output, Docker BuildKit cache) while four agent sessions and live Postgres
test containers kept running. The owner wants it on every device as part of Pakem, not
as one session's knowledge (2026-10-31).

## Decision

1. **A `pakem health` area**, stage `experimental`, beside Device (area 1). It covers the
   owner's devices: macOS, Linux, and WSL, like `pakem setup device`. Native Windows is
   refused with the same pointer to WSL. Servers are out of scope: release hosts keep the
   release engine's retention and label-scoped pruning.
2. **`pakem health check [--json]`, read-only and standard library.** Evidence for every
   probe below is the health agent's handoff, checked on the owner's MacBook on
   2026-10-31. It reports:
   - hardware and OS (model, CPU, memory, macOS or kernel version), battery and thermal
     state where the platform exposes them, disk capacity and use.
   - agent sessions and build processes running, and listening ports.
   - Docker: running containers, named volumes, anonymous volumes with and without a
     container, tagged and dangling images, build cache size.
   - build output under the configured workspace roots: Rust `target/` (with
     `debug/incremental` split by age), `.venv`, `node_modules`, `.next`, and similar.
   Each finding has a stable id, size, age, path or Docker object, a tier, and an
   `in use` flag (a live process inside it, a running container using it, or a worktree
   an agent session is in). A probe a platform lacks reports `unknown`, never an error.
   How it measures, from the handoff's traps:
   - Sizes are allocated blocks (`st_blocks * 512`), never `st_size`: `Docker.raw` reports
     228 GiB and occupies 21 GiB. Hard links count once.
   - Free and used space come from `os.statvfs` on the data volume (macOS
     `/System/Volumes/Data`, the APFS container from `diskutil apfs list -plist`). Never a
     recursive `du` of the home folder or `~/Library`, which takes about a minute: only
     the configured roots are scanned, for known build-output names, and `incremental`
     ages come from each `<crate>-<hash>` folder's own modification time.
   - macOS: `system_profiler -json`, `pmset -g batt|therm`, `vm_stat`, `lsof -a -p <pids>
     -d cwd` for session folders (`-a` keeps it under a second), `lsof -iTCP -sTCP:LISTEN
     -P -n` for ports. Linux and WSL: `/proc` and `/sys` (`meminfo`, `loadavg`,
     `power_supply`, `thermal`, `<pid>/cwd`, `net/tcp`), no external tools.
   - Urgency from free space on the data volume: critical below 10% or 20 GiB, healthy
     above 15% and 30 GiB, watch in between (configurable).
   - The module never reads or names secret files: a shell one-liner mentioning `.env`
     is blocked by the guard, and the check has no reason to look at them.
3. **Three tiers**, set by the tool only where the rule is unambiguous:
   - **Zero-impact garbage:** `target/<profile>/incremental/<crate>-<hash>` folders older
     than 24 hours while no `cargo` or `rustc` runs in that workspace (23 GB of the 30 GB
     the health agent reclaimed came from here), Docker build cache older than 24 hours, untagged images,
     and `brew cleanup` (Homebrew's own command).
   - **Warm caches:** regenerable but costly: cross-compile `target/<triple>` folders idle
     for 48 hours, anonymous volumes (64-hex names) mounted by no container, running or
     stopped, tagged images no container uses, `.venv`, `node_modules`, `.next` of idle
     projects. Anonymous volumes form one finding, re-checked volume by volume on clean.
     Idle Gradle and Kotlin daemons are reported for memory, never stopped.
   - **Cold:** large and idle, not rebuilt by a build (whole idle repositories, old
     toolchains). The tool never produces them: the skill leaves the judgment and any
     archiving to another machine to the owner.
   - **Never listed for cleanup:** named Docker volumes, even when `docker volume ls -f
     dangling=true` lists them (on the owner's Mac 22 of its 73 entries were project
     databases), `target/debug/deps`, `incremental` folders younger than 24 hours, git
     working trees and stashes, agent folders (`~/.claude`, `~/.codex`, `~/.gemini`),
     toolchains (`~/.rustup`, `~/.cargo/registry`), personal folders, `/tmp` (agent
     sessions run scripts from it, and the system clears it on restart).
4. **`pakem health clean <id>... [--plan] [--yes]`.** It deletes only findings from the
   latest check, named by id, and re-verifies each one immediately before deleting:
   still present, still not in use, still inside an allowed location, unchanged in kind.
   Folders are removed from the filesystem, never with `cargo clean`, whose lock on
   `target/.cargo-lock` would stall a live session's build. Docker objects are removed one
   by one after a fresh `docker inspect` (test containers came and went within minutes),
   never with `volume prune` or `system prune`. Build cache uses `docker builder prune
   --filter until=24h`. A refused finding is reported and skipped. Agents run it only for findings the owner approved in the
   session (kernel §2: ask before deleting what the task did not create).
5. **A core skill, `device-health`**, not a profile: it holds the same on every device.
   It tells the agent to run the check first, weigh urgency against the hardware and the
   live sessions, present the findings by tier with the space each frees, get the owner's
   approval, and run `clean` for exactly the approved ids.
6. **Configuration** in `~/.config/pakem/health.toml`, device-local: the workspace roots
   to scan and the thresholds (incremental, cross-target, and build-cache ages, urgency
   limits, scan depth). Without roots the check still reports the system and Docker and
   says how to add them.
7. **The weekly drift report** (`pakem schedule`) gains a read-only health summary on a
   device that has `health.toml`: disk urgency and reclaimable build output, as a warning
   when the disk is not healthy. Docker and Homebrew stay in the full check. It never cleans.
8. **Privacy.** The check's output names processes, paths, and ports. It stays on the
   device and is never published (kernel §6).

Considered and rejected:

- Cleanup by the agent's own shell commands. The kernel floors (exact targets, no broad
  paths) would then rest on each session. `clean` enforces them in one place.
- Health inside `pakem setup check`. Setup prepares a device, and health inspects a running
  one, and mixing them makes setup slow and its exit codes ambiguous.
- A profile or an optional add-on. Profiles carry per-project context rules, and this is the
  same on every device, so it ships with the core and reaches every device by
  `pakem update`.

## Consequences

- New: `tools/pakem_lib/health.py` (probes, per-platform collectors, tiers, clean, the
  last check kept in Pakem's state folder for 24 hours), `skills/device-health/`
  (SKILL.md and a tiers reference), a command map section, a report section, and tests
  that never reach the real Docker or Homebrew.
- No kernel line: the kernel is at its 150-line budget and the skill's description
  triggers it.
- Open until a real use proves them: exact thresholds per platform, Android and Xcode
  locations, Linux battery and thermal sources.
