- `pakem setup` guides a device from nothing to ready, and the one-line installer ends in
  it. It asks only what it cannot find out, with the device's current answer as each
  default: what updates follow, which agents to set up, which profiles (git repositories
  are cloned once, and each folder in them with a `profile.toml` counts; or a new one from
  the example), and the forges, containers, and update mode. Then it shows the plan, asks
  once, installs the kernel and skills, prepares the tools, forge logins, and device key,
  turns on each agent's guardrails, and ends with `pakem setup check`. Running the
  installer or `pakem setup` again reconfigures. Without a terminal, `--yes` takes the
  defaults and the flags as the answers.
- `install.sh --source DIR` uses a Pakem checkout you already have, and a re-run keeps a
  checkout that follows `main`, instead of moving it back to the latest release.
