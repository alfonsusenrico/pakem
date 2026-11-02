- Direct releases refuse to build when the server's Docker data root or the release base has
  less free space than the host config's `min_free_gb` (default 10 GB; 0 turns it off). The
  refusal names the free space, Docker's usage per kind, and how to free space; nothing is
  deleted. A kept commit builds nothing and is not checked.
