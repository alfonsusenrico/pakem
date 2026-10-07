# Personal Projects — Agent Scope

All projects under `/Users/enrico/enrico/project/` adopt the canonical owner
baseline at `/Users/enrico/project/AGENTS.md`. Read that file before meaningful
work, followed by the nearest project-specific `AGENTS.md` and `PROJECT_STATE.md`
when present.

This file intentionally does not duplicate the universal baseline. Project-local
instructions extend it with repository facts and constraints; they must not weaken
higher-authority security, quality, verification, or handoff requirements.
Dormant projects do not need a local `AGENTS.md` merely to repeat the baseline.
Create or update one when an active project has durable project-specific commands,
architecture, constraints, or workflow exceptions worth preserving.

## Personal deployment identity

- Never use a personal machine SSH identity for CI/CD, automation, or deployment.
- Create a dedicated, project-scoped deployment key with the minimum required
  access, authorize only its public key, and store its private key only in the
  approved secret mechanism.
- Never print or inspect private-key contents. Validate only safe metadata such as
  the expected path, ownership, permissions, fingerprint, and public-key identity.
