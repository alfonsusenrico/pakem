# agent-framework

Rules for agents editing this repository. The kernel itself is `kernel/KERNEL.md`;
it is installed into each host by `tools/af install`, not loaded from here.

## Purpose and boundaries

- This repository holds the owner's universal kernel, skills, standards,
  templates, and tooling. It must stay free of organization- and client-specific
  content; that belongs in profile repositories (ADR 0001).
- Project business rules never belong here.

## Commands

- Tests: `python3 -m unittest discover -s tests -v`
- Lint: `tools/af lint` (must report 0 errors before any commit)
- Install preview: `tools/af install` (dry run; `--apply` only with owner approval)
- Python 3.11+ standard library only for `tools/`, `enforcement/`, skill scripts.
  Hooks are POSIX sh and must work with macOS `/bin/sh`.

## Rules for changing the framework

- Kernel budget: at most 9,500 characters and 150 lines. Every line must be
  universal, not discoverable from code, not mechanically enforceable, and tied to
  a real or plausible failure. List rules removed or considered when adding one.
- `kernel/DIGEST.md` may contain only verbatim kernel lines (ADR 0003).
- Skills follow the Agent Skills format: trigger-first description of at most 350
  characters, body under 500 lines, references one level deep, each reference
  starting with a status header.
- Instruction text drafted by an agent needs owner review before it is merged.
- Record decisions as ADRs in `docs/adr/` and every release in `CHANGELOG.md`;
  bump `VERSION` per semver (MAJOR: floor, marker, or skill-name changes).
- `archive/` holds legacy sources verbatim; never edit or install from it.

## Overrides

- `default:openspec`: framework changes use ADRs, CHANGELOG, and semver instead of
  OpenSpec (ADR 0005).
