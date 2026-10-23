- The `pakem` skill tells every agent how to use the commands: conventions and exit codes,
  each area's stage, the interview-then-short-command flow for servers, projects and the
  gap protocol, releases, and proposals. The areas Proof 1 proved (status and updates,
  device, host, release for services) are `stable`, so agents use `pakem` for them and
  never hand-run the steps (ADR 0011, decisions 4 and 10); project and proposals stay
  `experimental`. The kernel's skills table gains the `pakem` row (its intro line is one
  line shorter, so the kernel stays at 150 lines).
