- `backend-engineering`: unknown fields are rejected on inputs the project defines;
  third-party responses and webhooks are validated on the fields used. Applied
  migrations must not change silently (runner checksum or a CI comparison), so
  runners without checksums qualify with that CI test.
- Light and dark themes apply to every UI; removed from the `deployed` capability.
- `owner-dashboard` and `owner-workbench` are marked "do not adopt until the owner
  approves it".
- Planning and bootstrap templates: the last task opens the merge request and
  reports its URL; force pushes need the owner.
