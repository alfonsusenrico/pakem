- The CI that `pakem project init` writes no longer runs on release tags. `pakem release`
  reads the checks of the commit it deploys, which the push to `main` already ran, and it
  pushes the tag only after deploying. A tag run repeated the main run, held a runner, and
  while it ran, or after a flaky failure, made the next release of that commit refuse
  (ADR 0010, decision 2 amended).
- Projects: remove `tags: ["v*"]` from the `push` trigger of the CI workflow that runs
  checks. Keep it in workflows that release or deploy from a tag.
