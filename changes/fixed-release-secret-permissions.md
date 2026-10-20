- Compose file secrets keep the host file's owner and mode, so containers running as a
  non-root user could not read them on Linux hosts. Secret files are now 0640 (the deploy
  user and its group), the engine exports `AF_SECRETS_GID`, and services that read secrets
  declare `group_add: ["${AF_SECRETS_GID}"]`; existing 0600 secrets are opened up to the
  group on the next deploy.
