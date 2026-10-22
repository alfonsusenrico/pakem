- `pakem setup host <target> --project-dir DIR` is the one owner step before releasing: it
  makes the device key if missing, installs or updates the engine, and lets the key release
  each project (switching registry environments to direct delivery), over one connection.
  The owner actions of `pakem release`, `project init`, and `project adopt` name that one
  command; `pakem setup key` and `pakem host allow-device` stay for single steps later.
