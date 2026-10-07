# Agent Tooling Policy

Status: Active
Applies to: skills, plugins, MCP servers, hooks, and agent tools
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline (2026-09); owner skill review (2026-09); framework ADRs

- Keep the global skill set small and owned. Installing or enabling any skill, plugin,
  hook, MCP server, telemetry, or provider routing needs the owner's approval.
- Never adopt tools that compress reasoning, impose shortest-diff behaviour, add
  self-scores or branding to code, or impose a universal visual style.
- OpenSpec: native CLI and generated integrations only (`change-planning`).
- Code-graph tools (for example Graphify): navigation aids only. Check root, coverage,
  and freshness; missing edges do not prove missing callers; verify findings against
  source; prefer direct reads and `rg` for known files.
- Third-party design skills (for example `frontend-design`): may inform direction but
  never override the project's approved design or the owner's design base.
- Review upstream changes before updating any tool and preserve local customizations.
- Do not claim a tool or skill ran when it was unavailable; apply the equivalent
  check manually and say so.
