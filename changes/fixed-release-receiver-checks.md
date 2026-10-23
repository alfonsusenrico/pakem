- `pakem release rollback previous` returns the last release that was live and working
  before the running one, and never one a rollback left: after A, B, a rollback to A, and
  C, it returns A, not B. A host's receiver now records rollbacks with trigger `rollback`.
- The receiver refuses a publish to production and a promotion to any other environment,
  whatever the client checked.
- An image's `context` and `dockerfile` in `deploy/release.toml` must be relative paths
  inside the repository; the receiver also refuses either when it resolves outside the
  commit's tree. An absolute context could bake another project's secrets into an image.
