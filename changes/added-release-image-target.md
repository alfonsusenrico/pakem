- `[[images]]` entries in `deploy/release.toml` take an optional `target`, a Dockerfile
  build stage, so several images can come from one Dockerfile (for example an
  application image and a backup image with extra tools).
