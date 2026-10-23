- `pakem status` always exits 0: an unreadable config or another error becomes a line in
  its report. It notes when the installed commit is not on `origin/main` (an install
  from an unmerged branch), and the mid-session hook names the `pakem-update` skill.
- `pakem update` shows what the install changes (kernel, skills, tool, hooks) before it
  asks, and a failed fetch exits 5 (check failed) instead of 2 (usage).
