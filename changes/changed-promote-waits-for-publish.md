- Promotion waits for the commit's release to be published (`af_publish.py promote
  --wait-minutes`, `publish_wait_minutes` input of the GitHub release workflow, default 30)
  instead of failing when a tag is pushed while the merge's publish is still running; only a
  missing record is retried, every other registry error fails at once.
