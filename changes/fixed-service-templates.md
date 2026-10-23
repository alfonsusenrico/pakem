- Service starter files: the Dockerfiles pin their base images by digest (the uv and Node
  images in `PINS.md`), the Node image keeps production dependencies and the build output
  only, a `.dockerignore` keeps `.git`, `.env` files, local environments, and research
  out of the build context, and `.env.example` lists `DATABASE_URL` only for a project
  with the database capability.
