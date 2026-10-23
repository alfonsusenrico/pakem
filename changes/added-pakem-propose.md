- `pakem propose new | revise | list | show | reject`: the agent that discussed a case
  with the owner writes a proposal, with the exact change drafted against the framework's
  `main`, as a pull request labelled `proposal` (lint and unit tests pass on its branch
  first); the owner's merge accepts it, `revise` answers a change request with a new
  commit, and `reject` records the reason and closes it. ADR 0007 is amended: each
  accepted proposal applies when merged and devices run `pakem update`.
