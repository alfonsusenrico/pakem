- Kernel default `default:no-billed`: agents use only free services and free tiers; a
  project approves a specific billed service, with its budget, in `## Overrides`.
- ADR 0010 (Proposed): builders and deployment triggers are each project's choice (local
  device, self-hosted GitLab, free GitHub runners), checks run separately from releases,
  and a direct delivery mode sends signed releases over a restricted SSH receiver.
