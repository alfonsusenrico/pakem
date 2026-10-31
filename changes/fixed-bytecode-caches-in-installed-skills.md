- `pakem install` and `pakem update` no longer block with `BLOCKED-locally-edited` after an
  agent runs an installed skill's Python script: `__pycache__` folders are neither copied
  nor hashed, and manifests from older installs that hashed them still match.
  `pakem doctor` and `pakem uninstall` compare the same way.
