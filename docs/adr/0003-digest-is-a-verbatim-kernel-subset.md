# 0003. The pinned project digest is a verbatim kernel subset

- Status: Superseded by 0007
- Date: 2026-10-07

## Context

Cloud agents, CI agents, colleagues, and fresh machines do not have the kernel
installed, so each repository needs the floors in its own `AGENTS.md`. The previous
hand-copied block could drift from the source and could contradict it.

## Decision

`kernel/DIGEST.md` contains only lines that appear verbatim in `kernel/KERNEL.md`
(lint-enforced). `pakem project sync` renders it into each repository between markers
carrying the framework version and a sha256 of the body; `pakem project check` and a
CI script verify integrity.

## Consequences

The repository copy can lag behind the kernel but can never contradict it. When the
kernel is also installed, roughly 1.6 KB of text repeats, which is accepted as the
price of portability.
