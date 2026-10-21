- The framework is renamed Pakem. The command is `tools/pakem`, with `tools/pk` as an
  identical short alias; the former `af` entry point and command are removed, and every
  documented `af <command>` is now `pakem <command>`. The library is `tools/pakem_lib`.
- Skill `af-update` is now `pakem-update` (`/pakem-update` in Claude Code); `pakem install`
  prunes an installed `af-update` copy like any skill whose source is gone.
- Installed state uses the new name: `~/.local/state/pakem` (manifest, backups, reports),
  `~/.local/share/pakem/guard.py`, launchd job `com.pakem.drift-report`,
  `~/.codex/rules/pakem.rules`, and the Antigravity hook group `pakem-guard`.
- A device installed under the former name is migrated automatically by the first
  `pakem install --apply` (and by `uninstall`, `enforce`, or `schedule` with `--apply`):
  the state and shared-data directories are moved, an unedited guard is refreshed, the
  guard hooks recorded in Claude Code and Antigravity settings point at the moved guard
  (each file is backed up first), the recorded Codex rules file is moved, and the recorded
  launchd job is replaced. Edited or unrecorded files are reported and left alone, a dry
  run prints the plan, a second run changes nothing, and `pakem doctor` warns until done.
- `pakem report` reads `~/.config/pakem/report.toml`; a config still at
  `~/.config/agent-framework/report.toml` is read and reported, never moved.
- The installed kernel header reads `pakem kernel`, and pinned blocks are written as
  `<!-- pakem:profile ... -->`. Blocks with the former `agent-framework:` marker are still
  read (also by `tools/check_pinned_blocks.sh`); `pakem project check` warns and
  `pakem project sync --profile <dir>` rewrites them.
- Protected branches use the git config key `pakem.protectedBranch`. The pre-push hook and
  the guard fall back to `agentframework.protectedBranch`; `pakem hooks install` copies it
  to the new key and keeps the former key equal for hooks copied before the rename.
- The release engine keeps its runtime identifiers for now (`af_*.py`, the `af-agent` and
  `af-monitor` units, `/etc/af`, `/opt/af`, the deploy user `af`, `AF_*` variables,
  `af.release/v1`, `application/vnd.af.*`, `deploy/af/`, `af-release.yml`, `af-push.yml`);
  they are renamed later with their own migration.
