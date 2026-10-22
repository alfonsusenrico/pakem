- `pakem propose new | revise | list | show | accept | reject`: the agent that discussed a
  case with the owner writes a proposal, with the exact change drafted against the
  framework's `main`, as a GitHub issue for the owner's review; on acceptance, `accept`
  opens its own pull request from a separate worktree after lint and tests. ADR 0007 is
  amended: each accepted proposal applies when merged and devices run `pakem update`;
  version releases still batch the CHANGELOG.
