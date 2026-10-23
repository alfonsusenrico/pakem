# Pakem

Rules for agents editing this repository. The kernel itself is `kernel/KERNEL.md`;
it is installed into each host by `tools/pakem install`, not loaded from here.

## Purpose and boundaries

- This repository holds the owner's universal kernel, skills, standards,
  templates, and tooling. It must stay free of organization- and client-specific
  content; that belongs in profile repositories (ADR 0001).
- Project business rules never belong here.

## Commands

- Tests: `python3 -m unittest discover -s tests -v`
- Lint: `tools/pakem lint` (must report 0 errors before any commit)
- Install preview: `tools/pakem install` (dry run; `--apply` only with owner approval)
- Python 3.11+ standard library only for `tools/`, `enforcement/`, skill scripts.
  Hooks are POSIX sh and must work with macOS `/bin/sh`.

## Rules for changing the framework

- Kernel budget: at most 9,500 characters and 150 lines. Every line must be
  universal, not discoverable from code, not mechanically enforceable, and tied to
  a real or plausible failure. List rules removed or considered when adding one.
- Skills follow the Agent Skills format: trigger-first description of at most 350
  characters, body under 500 lines, references one level deep, each reference
  starting with a status header.
- Instruction text drafted by an agent needs owner review before it is merged.
- The framework is stable (ADR 0007, amended 2026-10-22). Change rules only for a
  problem a real project hit, through `pakem propose`: each accepted proposal is its own
  PR. The `pakem` tool releases whenever an area is proven; kernel and skill text waits for
  the next release, batched at most once a month, and never triggers one alone (ADR 0011).
  Build no new `pakem` mechanism unless a project needs it.
- ADRs only for decisions with real trade-offs. Record each change as a fragment
  `changes/<type>-<kebab-topic>.md` (`changes/README.md`); never edit `CHANGELOG.md`
  on a branch. A release runs `tools/pakem changelog --release X.Y.Z --apply`, which
  writes `CHANGELOG.md` and `VERSION`. MAJOR only when a floor changes or an
  existing install or project would break; MINOR for new or changed rules; PATCH
  for fixes.
- Compliance evals are optional: run them by hand after a large host or model
  change, not as a release gate (`evals/README.md`).

## Overrides

- `default:openspec`: framework changes use ADRs, CHANGELOG, and semver instead of
  OpenSpec (ADR 0005).
