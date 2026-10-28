- `pakem update` (and `pakem status`) fetch with `--prune`, so a branch the forge already
  deleted on merge is no longer planned for remote deletion, which used to end in a
  needless warning. The update reports only the branches actually deleted, and names the
  ones origin refused.
