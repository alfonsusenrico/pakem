# Cleanup Tiers

Status: Active
Applies to: every finding of `pakem health check`
Last reviewed: 2026-10-31
Review by: 2027-04-30
Sources: framework ADR 0016, and the health agent's audits of 2026-10-28 and 2026-10-31 on the owner's MacBook (about 30 GB reclaimed with four sessions and live test databases running)

## Zero-impact

Regenerates on the next build at no visible cost.

| Finding | Rule |
| --- | --- |
| `rust-incremental` | `target/<profile>/incremental/<crate>-<hash>` folders untouched for 24 hours while no `cargo` or `rustc` runs in the project. Cargo orphans a hash folder whenever flags, features, or branches change. An idle agent session in the project does not block it. |
| `docker-build-cache` | Build cache not used for 24 hours. It only speeds up later builds. |
| `docker-image-dangling` | Untagged images no container uses. |
| `brew-cleanup` | Homebrew's own `brew cleanup`: old versions and downloads. |

## Warm

Regenerable, but losing it costs a rebuild, a reinstall, or a pull.

| Finding | Rule |
| --- | --- |
| `rust-cross-target` | `target/<triple>` output idle for 48 hours: minutes to rebuild for that platform. |
| `build-node_modules`, `build-.venv`, `build-.next` | Dependencies and build output: a reinstall or rebuild. In use while an agent or build runs in the project. |
| `docker-image` | Tagged images no container uses. Often tools a lint or test run pulls again, slowly. |
| `docker-volumes` | Anonymous (64-hex) volumes mounted by no container, running or stopped. Test containers create and drop them within minutes, so clean re-checks each volume against a fresh container list. |

## Never findings

- Named Docker volumes: they hold local databases even while their containers are stopped,
  and `docker volume ls -f dangling=true` lists them anyway (22 of 73 on the owner's Mac).
- `target/*/deps` and `incremental` folders younger than the threshold: a live session's
  next build needs them.
- Git working trees, untracked files, and stashes.
- Agent folders (`~/.claude`, `~/.codex`, `~/.gemini`), toolchains (`~/.rustup`,
  `~/.cargo/registry`), personal folders.
- `/tmp`: agent sessions run scripts from their scratch folders there, and the system
  clears it on restart.

## Sizes and speed

Sizes are allocated blocks, never the apparent size: Docker Desktop's disk image reported
228 GB and occupied 21 GB. A full check takes seconds to tens of seconds, most of it summing
large build folders, because it scans only the workspace roots for known build-output names.
