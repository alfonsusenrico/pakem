- `pakem host runner add <host> <repo> --count N` works for a repository that already has a
  runner. It used to reconfigure the registered instance, which the GitHub runner refuses
  ("already configured"), so no runner was added. Registered instances are now kept as they
  are, and only the missing ones are registered.
