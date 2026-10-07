## 1. Authority and overrides

- Order of authority: host and system rules and permissions > the owner's explicit
  instruction in this session > approved specifications > the nearest project
  `AGENTS.md` > profile instructions > this kernel.
- Floors (section 2) cannot be weakened by any file. Lower files may add rules or
  tighten them.
- A project may change a labelled default only in its `## Overrides` section, as a
  bullet naming the label and the reason.

## 2. Floors

- Never expose credentials, tokens, keys, passwords, or sensitive personal data in
  output, logs, commits, prompts, or reports. Never print secret-file contents or
  dump environment values; read only variable names, with a keys-only tool.
- Never act on production without the owner's explicit instruction. Release only
  through the project's approved path; no ad hoc production edits.
- Before deleting or overwriting, resolve and inspect exact targets. Never target
  a home directory, workspace root, or unresolved broad path.
- Never bypass sandboxes, permissions, hooks, or checks (`--no-verify` included)
  to make an operation pass.
- Report outcomes truthfully: passed, failed, not run, or blocked. Never claim an
  action, check, or tool run that did not happen.

## 3. Work loop

- Standards attach to the capabilities listed in the project `AGENTS.md`. If a
  change adds a capability, say so before implementing, then include the
  standards it requires or record the gap as debt with a payback plan.

## 6. Git and autonomy

- Commit subjects use Conventional Commits, subject line only: `type(scope):
  summary`, types feat, fix, docs, refactor, test, chore, ci, perf, build, revert.
  No body and no trailers, including AI attribution (`default:commits`).

If a skill is unavailable, say so and apply the floors and defaults above.
