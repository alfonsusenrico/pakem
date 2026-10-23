- `pakem project init` and `adopt` write CI for `Delivery: others` too, as for any
  project (owner decision, 2026-10-23). A client that runs its own CI opts out with
  `--client-ci WHERE`, which writes no CI and records gap `ci.owner`; the gap stays open
  until the Pakem `ci.yml` exists or the decision names the client's CI. Tag protection is
  asked only for projects we release, and no longer when GitHub could not be asked.
