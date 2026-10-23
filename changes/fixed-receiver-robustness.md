- The release receiver takes the environment's deployment lock before it builds, so a
  second release for the same environment is busy at once. Each signed request runs only
  once while fresh, and one more than 2 minutes ahead of the host's clock is refused.
- A failed or timed-out build sends a `failed` notice, and unexpected errors end with a
  message instead of a traceback.
- `pakem host revoke-device` reports (exit 6) when the host had no matching key line.
- An environment switched from the registry can republish the commit it runs, and the
  images it pulled from the registry are removed.
- Run `pakem setup host <name>` to update each host.
