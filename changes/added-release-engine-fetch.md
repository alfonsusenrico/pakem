- `release-delivery` engine (contracts v1, draft): `af_oci.py`, a standard-library OCI
  registry client that verifies every manifest and blob against its digest, and
  `af_release.py`, which resolves an environment pointer, checks cosign signatures on the
  record, images, and bundle, refuses older releases without the rollback flag, and unpacks
  the deploy bundle (files and directories only, no `.env` files).
