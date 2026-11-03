- `pakem project check` warns about each `docker image prune` or `docker system prune`
  without a label filter in CI pipelines and deploy scripts.
- Projects: replace each unfiltered prune the check names with clean-up scoped to the
  project's own images (by tag or label).
