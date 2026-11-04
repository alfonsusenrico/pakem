- `pakem migrate export` and `import` carry a workspace to a new device: the Pakem settings
  (never `device.toml`, and servers without the old device's allowed projects), the install
  choices, each known project's place, remote, and branch with its `PROJECT_STATE.md` and
  `JOURNAL.md`, the profile repositories, the documents store, and with `--agent-sessions`
  Claude Code's folder for each project. The bundle is one private file the owner moves by
  hand, with paths relative to the home folder. Import clones what is missing to the same
  places and overwrites nothing. On a fresh device, `pakem setup` asks for the bundle
  first (or `--import FILE`), so its later questions start from the old device's answers.
