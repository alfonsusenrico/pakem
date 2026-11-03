# 0018. Host limits come from measurement or the owner, never from one host

- Status: Accepted (owner, 2026-11-03)
- Date: 2026-11-03

## Context

ADR 0011 says the core "never contains project- or case-specific logic, names, or
infrastructure" and keeps pending choices as "settings instead of constants". Pakem's host
limits broke that rule: they were numbers chosen on the owner's first server.

- **Builder:** the release builder was fixed at 2 CPUs and 4 GB.
- **Build cache:** it was pruned by age only (7 days), and a failed build skipped the prune.
- **Disk reserve:** builds needed a flat 10 GB free.
- **Runner slice:** at least 2 CPUs and 4 GiB.
- **Runner cache and images:** a 10 GB cache cap and a 72-hour image age, at most 8 runners per
  repository.
- **No alert:** nothing warned before a disk filled.

On 2026-11-03 a release host with a 110 GB root disk reached 87% use. Its builder's cache held
9.7 GB, all reclaimable, under BuildKit's own default floor of 10 GB, and no setting could
bound it.

The same numbers are wrong in both directions:
- On a 2 GB VPS the builder's memory limit exceeds the machine.
- On a 2 TB server 10 GB is no reserve at all.

The owner may also move to a larger device or a real server, and other people may use
Pakem on hosts nobody here has seen. The runner's environment overrides did not help: `sudo`
dropped them, and re-running the install reset them.

## Decision

1. **Mechanisms and rules, no capacity numbers.** Pakem ships the mechanism and the rule that
   derives each limit from what the host has: its CPUs, its memory, the size and free space of
   the volume concerned, and the measured size of recent builds. Limits are recomputed on every
   run and never stored, so a bigger disk or a new server changes them without an edit. The
   rules are shares, not sizes (contracts §14).
2. **One precedence rule.** A value the owner sets for a host (`pakem host limit`, written to
   `/etc/pakem/host.toml` by `pakem setup host`) wins over the derived one. An environment's
   own `min_free_gb` in its host config wins for that environment's builds. Nothing else
   overrides a limit.
3. **Derivation by purpose:**
   - **Build cache cap:** the smaller of a share of the volume and half the space left above
     the reserve, recomputed after every build (failed or not) and every daily clean-up, so the
     cache shrinks as the disk fills.
   - **Build reserve:** the largest recent build with a margin, never below a small share of
     the volume. Before any build is measured, a larger share of the volume.
   - **Disk alert:** a level tied above the reserve, so it always fires before a release is
     refused, plus the time until the free-space trend reaches the reserve. `af-monitor`
     notifies `disk_low` and `disk_recovered` through the environments' channels.
   - **Builder and runner CPU and memory:** shares of the host with no absolute floor. The
     builder's and the runner's memory shares together leave production at least 30%.
   - **Runners per repository:** from the CPUs of the runner's slice.
4. **Build footprint is measured from Pakem's own builder.** It is BuildKit's cache growth plus
   the images the build loaded. Free space during the build would count CI jobs and anything
   else on the volume, but this measure counts only the release. A live check on 2026-11-03
   (buildx 0.38, BuildKit 0.33) recorded 195 MB for a cold build and 97.5 MB for a warm one.
   This is conservative, because shared base layers count in full. A host with no builds yet
   uses the proportional rule.
5. **Owner values are checked where they are set.** The device validates them against the host
   module's own schema before recording, and refuses an alert level that is not above the
   reserve. The host checks them again before installing them.

## Consequences

- One module, `af_limits.py`, holds the rules. The receiver, the runner script, its daily
  clean-up and the monitor read it, and `af_limits.py show` (in `pakem setup check --host`)
  names each limit's value and source.
- Existing hosts take the change at the next `pakem setup host`. Their builder is re-created
  once if its limits differ (losing its cache once), and the runner's slice and clean-up follow
  the host.
- **Unchanged:** a host config's `min_free_gb` still works. A host config without it now gets
  the derived reserve instead of 10 GB. On a 110 GB volume that is about 11 GB before any build
  is measured.
- **Retention:** the engine's default `keep` now matches `release_host.py` (production 6, others
  3). Every Pakem-generated `deploy/release.toml` already sets `keep`.
- **Shares remain as constants,** and they are design choices. If a real host shows one is
  wrong, the owner sets that host's value, and a fix to the share goes through `pakem propose`.
- **Still not bounded by size:** the per-project retention by count, project image size, and
  `pakem health` thresholds on devices. They are separate work.
