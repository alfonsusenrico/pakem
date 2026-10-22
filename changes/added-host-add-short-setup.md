- `pakem host add <name>` records a server's options on this device only, so an agent can
  interview the owner and record the answers; the owner's step becomes `pakem setup host
  <name>`, which reuses everything recorded (flags only change it). `pakem setup host` with
  no host asks the questions on a terminal; run by an agent, it records what it was given
  and names the short command. Owner actions from `release`, `project init`, and
  `project adopt` use that form. Fixed: a re-run without flags no longer resets recorded
  options to defaults, and `--name` always picks the host it names.
