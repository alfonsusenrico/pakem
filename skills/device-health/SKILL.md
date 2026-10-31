---
name: device-health
description: Use when the owner's device is low on disk, slow, hot, or swapping, when asked for a health check or a cleanup of build output, caches, or Docker on the device, or before a large build. Runs `pakem health check`, explains findings by tier, and removes only owner-approved findings with `pakem health clean`.
---

# Device Health

`pakem health` inspects the device and removes what the owner approves. The judgment is
yours, the deleting is the tool's: it re-verifies every finding at the moment it acts.

## 1. Check first

Run `pakem health check` (add `--json` to read it programmatically). Never hand-run a
recursive `du` over the home folder or `~/Library`, which takes about a minute, and never
clean by hand: no `docker volume prune` or `docker system prune` (they delete named
database volumes), no `cargo clean` (its lock stalls a live session's build), no `rm -rf`
on a guess. If the check has no workspace roots, add the owner's workspace folders as
`roots` in `~/.config/pakem/health.toml` and run it again.

## 2. Weigh urgency

The check rates the data volume `critical`, `watch`, or `healthy` by free space. Weigh it
against the rest of the report: memory free and swap, thermal state, battery, and the
agent sessions and builds running. A critical disk with swap in use needs space now. A
healthy device needs nothing unless the owner asks.

## 3. Present the findings

Follow [tiers](references/tiers.md). For each tier, give the total and the largest
findings with their ids, what each costs to lose (nothing, a rebuild, a pull), and what is
in use. Name what stays protected (for example the named Docker volumes). Recommend:

- critical: the zero-impact tier, plus the warm findings that free the most for the least
  rebuild cost.
- watch: the zero-impact tier.
- healthy: nothing.

Cold candidates (whole idle repositories, old toolchains) never come from the tool. If
you see them, suggest archiving them to another machine and leave the decision to the owner.

## 4. Clean only what the owner approved

Run `pakem health clean <id>... --plan` with exactly the ids the owner approved in this
session, show it, then run it again with `--yes`. Report what was removed, what was
skipped and why (in use, changed, refused), and run `pakem health check` again to show the
new free space. Never pass an id the owner did not approve.

## 5. Memory

The check lists idle Gradle and Kotlin daemons with the memory they hold. Stopping one
(`./gradlew --stop` in its project) is the owner's call. Never stop an agent session or a
process you did not start.

## 6. Platform notes

- macOS with Docker Desktop: Docker's disk image shrinks after a cleanup only as the VM
  returns blocks. The check shows its allocated and reported sizes.
- WSL: deleting files frees space inside Linux, but the Windows-side `ext4.vhdx` does not
  shrink by itself. Compacting it (`wsl --manage <distro> --optimize`) is an owner action
  on the Windows side.
