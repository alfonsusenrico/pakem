- `pakem project adopt` asks to remove only the registry publisher
  (`deploy/af/af_publish.py`) and signing key, not `deploy/af/contracts.py`, which a
  project's own checks may still run (found adopting personal-agent, Proof 1).
