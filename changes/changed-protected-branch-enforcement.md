- Pushes to protected branches are decided by the repository's pre-push hook: Claude
  Code deny rules, the guard, and Codex rules no longer block plain pushes to `main`;
  they still block force-pushing or deleting protected branches, and other force
  pushes need the owner.
- `af hooks install --protect-none` for projects whose `## Overrides` allow direct
  pushes; `af project check` warns when the framework hooks are not active.
