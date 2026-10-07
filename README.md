# agent-framework

A personal, versioned framework that coding agents read so every project is built
to the same standard, whatever the employer, client, or host tool.

## Layers

| Layer | Location | Loaded |
| --- | --- | --- |
| Kernel | `kernel/KERNEL.md` | Always, installed into each host's global instructions |
| Skills | `skills/<name>/` | On demand, when a task matches a skill description |
| Standards | `skills/*/references/`, indexed in `standards/` | When a skill points to them |
| Profiles | separate repositories (`profile.toml`) | Organization or client how-tos, opt-in |
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
tools/af doctor --repos <dir>       # drift and duplicate report
```

Decisions live in `docs/adr/`; changes in `CHANGELOG.md`.
