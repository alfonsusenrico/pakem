- `pakem enforce install --host codex` also merges `~/.codex/hooks.json`: `SessionStart`
  prints the brief status and `UserPromptSubmit` names the files to re-read after a
  mid-session install. Codex runs them once the owner trusts them in `/hooks`.
- `pakem enforce install --host antigravity` adds the `pakem-status` hook (`SessionStart`
  and `PreInvocation`), answering with `injectSteps`.
- `pakem update` refreshes Codex's rules and hooks together.
