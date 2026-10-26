- Deployment step 9 now limits image clean-up to the project's own images (by tag or
  label) and forbids an unfiltered `docker image prune` or `docker system prune` on a
  shared host.
