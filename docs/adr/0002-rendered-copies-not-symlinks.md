# 0002. Install rendered copies, not symlinks

- Status: Accepted
- Date: 2026-10-07

## Context

The previous setup symlinked `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`, and the
OpenCode global file to a workspace `AGENTS.md`. Claude Code then loaded the same
file twice (user file plus ancestor `AGENTS.md`), Codex never saw it, some hosts
skip symlinked instruction files, and edits made through any link silently
changed every host.

## Decision

`pakem install` writes a rendered copy of the kernel, with a version and hash header,
to each host's global instruction path, and copies skills into each host's skill
directory. A manifest records every managed path and hash; unmanaged files are
backed up before replacement.

## Consequences

Every host loads the kernel exactly once from its own documented location.
Local edits to installed copies are detected and block reinstall. Updating
requires running `pakem install` after changing the framework.
