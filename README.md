# agent-framework

A personal, versioned framework that coding agents read so every project is built
to the same standard, whatever the employer, client, or host tool.

## Layers

| Layer | Location | Loaded |
| --- | --- | --- |
| Kernel | `kernel/KERNEL.md` | Always, installed into each host's global instructions |
| Skills | `skills/<name>/` | On demand, when a task matches a skill description |
| Standards | `skills/*/references/`, indexed in `standards/` | When a skill points to them |
| Profiles | `profiles/personal/` (freelance practice); organization profiles in separate repositories (`profile.toml`) | Organization, client, or freelance how-tos, opt-in |
| Project | each repository's `AGENTS.md` | Pinned digest block plus project facts |

Precedence: host and system rules > owner instruction > approved specs > project
`AGENTS.md` > profile > kernel. Floors cannot be weakened; labelled defaults can be
overridden in a project's `## Overrides` section.

## Supported hosts

Claude Code, Codex, and Antigravity CLI are installed and verified
(`hosts/targets.toml`, `docs/host-matrix.md`). Other tools that read `AGENTS.md` and
Agent Skills work on a best-effort basis.

## Use

```sh
tools/af lint                       # check the framework itself
tools/af lint --profile <dir>       # also check a profile directory (repeatable)
tools/af install                    # preview what would be installed
tools/af install --apply            # install kernel and skills (owner approval)
tools/af project sync <repo>        # preview pinned blocks for a project
tools/af hooks install <repo>       # preview commit-msg / pre-push hooks
tools/af doctor --repos <dir>       # drift, duplicate, and orphaned-skill report
```

`af install --apply` installs from merged `main` only: the checkout must be on `main`,
clean, and, when an `origin` remote exists, at the same commit as the local
`origin/main` ref (no fetch is made; fetch first so that ref is current). A dry run
prints the same findings as warnings. Install flags:

| Flag | Effect |
| --- | --- |
| `--hosts a,b` | limit to these host ids from `hosts/targets.toml` |
| `--profile DIR` | also install this profile's skills (repeatable) |
| `--force` | back up and replace, or back up and remove, locally edited managed files |
| `--allow-dirty` | allow uncommitted changes, or a framework root that is not a git work tree |
| `--allow-unmerged` | allow a branch other than `main`, a detached HEAD, or a HEAD that differs from `origin/main` |
| `--strict-source` | refuse `--apply` when a `--profile` git checkout is not on a clean `main` (default: warn) |

The two `--allow-*` flags are independent: testing a branch with uncommitted edits
needs both. They waive checks on the framework checkout only; profile findings are
warnings unless `--strict-source` is given.

Skills that `af` installed and whose source no longer exists are removed
(`remove-managed`) when their installed copy is unchanged; an edited copy blocks
the install unless `--force` backs it up first. Skills from a profile that is not
passed with `--profile` are kept. If any write fails, the run restores every target
it had already written and leaves the manifest unchanged.

Decisions live in `docs/adr/`; changes in `CHANGELOG.md`.
