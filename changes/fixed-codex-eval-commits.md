- `af eval run --host codex` adds the fixture's own `.git` as a writable sandbox root,
  so commit-format runs can commit; `workspace-write` otherwise keeps `.git` read-only
  and every commit failed on `.git/index.lock`.
