- Skills, the personal profile, and the working-standard description follow the
  one-merge-request workflow (client repositories: push and report the compare link once
  the owner accepts the work).
- `release-delivery` and the deployment contracts state ADR 0010: builders and triggers
  are each project's choice, checks run separately from releases, and nothing billed is
  used without the project's approval.
- `instruction-maintenance`: agent-host memory is never the only home of a rule; owner
  decisions that outlive a session go into the framework in the same piece of work.
- `backend-engineering`: release units share code freely inside themselves and depend on
  each other only through published, versioned contracts (an API or a package version);
  SDK or own thin client is the project's choice.
- Deployment contracts section 12, "Reaching other projects": same-host shared network,
  private network, or public URL, chosen per project; the unique-alias rule applies to
  shared networks.
- Kernel: deploy credentials are limited to the projects they act on (one per device for
  the owner's devices, one per project for shared builders); `AGENTS.md` is updated in
  the same change that alters what it states. ADR 0010 amended: one key per device,
  SSH signatures for direct delivery. Kernel kept at 150 lines by merging related
  bullets; no rule weakened.
