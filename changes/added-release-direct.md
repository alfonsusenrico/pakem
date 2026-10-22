- `pakem release publish | promote | rollback | history | status` release a service by
  direct delivery (ADR 0010): the device signs a request for the exact commit with its
  device key and sends it, with the commit's `git archive` when the host keeps no build
  of it, to the host's new receiver (`af_receive.py`, the device key's forced command).
  The host verifies the signature, builds the images once per commit in a resource-capped
  BuildKit container, and the engine deploys them; promotion and rollback reuse kept
  builds. Publishing refuses a commit whose GitHub checks did not pass, production needs
  `--production`, and `promote` tags the commit after it deploys.
- `pakem host allow-device | revoke-device` let a device's key release a project on a
  host, from the owner's terminal: the receiver writes the project's direct host configs
  and deploy bases from its own deploy files, a forced-command line, and an
  `allowed_signers` line, and names missing placement and secret settings.
- The engine takes direct environments (`delivery = "direct"`) beside registry ones; the
  pull agent skips them, and the bootstrap installs the receiver and checks buildx.
