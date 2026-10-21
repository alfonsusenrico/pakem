---
name: client-engagement
description: Use when working inside a client's or partner team's repository - setting up or resuming work there, ranking sources of truth, turning gaps into questions for the client's PM, following the team's branch model and conventions, preparing a meeting brief or decision log, or packaging a deployment for a teammate who deploys.
---

# Client Engagement

The owner is the only contact with the client and the client's team. Agents work
from recorded evidence, ask through the owner, and leave nothing of the owner's
process in the client's history. Kernel floors and `sensitive-operations` apply to
every client system.

## 1. Before the first change

Record in the project `AGENTS.md` (kept private):

- the engagement role: sole developer, or one workstream in the client's team, and
  which modules, tables, and APIs belong to other workstreams;
- who decides (client PM, owner) and who builds and deploys;
- the sources of truth and their order (section 2);
- the integration branch, the merge path, and the deploy path;
- client conventions that differ from owner defaults (section 4);
- `default:mr-policy` in `## Overrides`: agents push and report the compare link, and
  the owner opens the merge request, because it is visible to the client's team.

Then set up the repository:

1. Exclude the process artifacts and install the hooks locally, protecting the
   team's branches: `af hooks install <repo> --local-only --protect <integration>
   --protect main --apply` (`project-bootstrap`, `references/git.md`).
2. Set the identity from the profile digest unless an override says otherwise.
3. Run `git status --ignored` and confirm the process artifacts are listed as
   ignored, not untracked.

## 2. Sources of truth

Rank the sources, record the order, and treat conflicts as questions. Follow
[sources of truth](references/sources-of-truth.md) for the default order, how to
read a live product as evidence, and the question format. Never implement a business
rule that no ranked source states; implement only what the owner or PM assigned and
record the minimum assumption needed to stay compatible with other workstreams.

## 3. Branch model

- Branch from the latest integration branch; one topic branch and one merge request
  per agreed phase or page. Never stack sub-phase branches on each other.
- Before reporting a branch, check that it merges cleanly
  (`git merge-tree --write-tree origin/<integration> HEAD`) and that the project's
  build, tests, lint, and formatter pass.
- Once the owner accepts the work, push the topic branch and report the compare link;
  the owner opens the merge request. Never merge, rebase shared branches, or delete a teammate's branch.
- Leave teammates' history as it is, even where it breaks owner conventions.

## 4. Client conventions and owner standards

- In the client's repository, the client's conventions govern formatting, naming,
  libraries, language, commit style, and branch names where they are written down or
  consistently practised.
- Owner standards apply where the client is silent.
- Each departure from a labelled default goes in `## Overrides` as one bullet naming
  the label and the client rule as the reason. A convention that would breach a
  floor is raised with the owner, not followed.

## 5. Privacy

- Process artifacts stay local per the profile digest; check `git status` before
  every push.
- Never write client credentials, test-account passwords, tokens, or customer data
  to the repository, state files, notes, or logs.
- A lesson moves into the framework only as a general pattern with no client rule,
  name, or data (`instruction-maintenance`, lessons intake).

## 6. Meetings and decisions

Prepare meeting briefs and keep the decision log in `research/notes/` following
[meetings and decisions](references/meetings-and-decisions.md). A decision is
binding only once the owner records who made it and when; then it moves into the
OpenSpec change it governs.

## 7. Deployment handoff

When a teammate or the client deploys, build the package in
[deploy handoff](references/deploy-handoff.md) from a pushed commit on the
integration branch. Final delivery to the client follows the `release-delivery`
handover standard (`references/handover.md`).

## 8. Boundaries

Without the owner's explicit instruction for that task, never:

- send a message, comment, review, or issue to the client or the client's team
  (draft it for the owner instead);
- touch client servers, deployment consoles, or production;
- write to a shared database (read-only inspection of a shared development
  database also needs the owner's go-ahead, and its schema is evidence, not a
  contract);
- sign in to client accounts or third-party dashboards.
