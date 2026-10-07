# Host Matrix

Verified 2026-10-07. Re-verify quarterly; machine-readable targets live in
`hosts/targets.toml`.

| Host | Global instructions | Project instructions | User skills | Limits | Enforcement | Source |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code | `~/.claude/CLAUDE.md` (does not stop AGENTS.md loading) | `CLAUDE.md`, or `AGENTS.md` when no CLAUDE.md exists in cwd or above | `~/.claude/skills` | <200 lines recommended per file | `permissions.deny`, PreToolUse hooks | code.claude.com/docs/en/memory, /permissions, /hooks |
| Codex | `~/.codex/AGENTS.md` | `AGENTS.md` from git root down to cwd | `~/.agents/skills` | 32 KiB combined (`project_doc_max_bytes`) | `~/.codex/rules/*.rules` (`codex execpolicy check`) | developers.openai.com/codex/guides/agents-md; learn.chatgpt.com/docs/agent-configuration/rules |
| Antigravity CLI (agy 1.2.13) | `~/.gemini/GEMINI.md`; avoid multiple global rule files | `GEMINI.md`, `AGENTS.md`, `.agents/rules/*.md` walking to repo root | `~/.gemini/config/skills` | 24,000 bytes per rule file; 20,000-token rules budget | `~/.gemini/config/hooks.json` PreToolUse (Go regexp matchers) | agy built-in skill `agy-customizations` |

Best-effort hosts (Kiro, OpenCode, Gemini CLI, others) read `AGENTS.md` and Agent
Skills directories but are not installed or verified by `af`.
