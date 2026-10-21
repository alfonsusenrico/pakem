- Skills, the personal profile, and the working-standard description follow the
  one-merge-request workflow (client repositories: push and report the compare link once
  the owner accepts the work).
- `release-delivery` and the deployment contracts state ADR 0010: builders and triggers
  are each project's choice, checks run separately from releases, and nothing billed is
  used without the project's approval.
- `instruction-maintenance`: agent-host memory is never the only home of a rule; owner
  decisions that outlive a session go into the framework in the same piece of work.
- `backend-engineering`: projects use each other only through published API contracts
  with their own thin clients, tested against recorded responses or published artifacts.
- The kernel is back to 150 lines (two bullets merged, the skill fallback folded into
  the skills sentence; no rule changed).
