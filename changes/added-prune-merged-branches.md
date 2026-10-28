- Kernel §6 default `default:prune-merged`: once a merge request merges, its branch is
  deleted locally and on the remote. A project opts out in `## Overrides` or keeps single
  branches with a `Keep branches:` line in `AGENTS.md`. `pakem update` prunes the framework
  checkout the same way, even when nothing else changed. Only a branch whose tip is part of
  the default branch counts as merged, so a squash-merged branch is kept. Kernel §4's
  cleanup line is shortened to make room.
