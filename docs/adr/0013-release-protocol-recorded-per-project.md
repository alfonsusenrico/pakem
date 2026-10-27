# 0013. Release protocol recorded per project

- Status: Proposed
- Date: 2026-10-27

## Context

The owner releases projects of several kinds: services through `pakem release` (direct
delivery), desktop and mobile installers through a packaging tool triggered by a version
tag, and projects whose release the owner does by hand. The steps, version scheme, and
notes differ per project, and the owner does not want to remember each sequence. Agents
re-learned the steps every time, and nothing kept agents on different devices or hosts
releasing the same way.

## Decision

1. Each project with `Delivery: us` keeps its release protocol in a `## Release` section
   of its `AGENTS.md`: who releases, the kind, the trigger, the version scheme, the notes,
   the checks before, the ordered steps, staging and soak, verification, and rollback
   (`release-delivery`, `references/release-protocol.md`).
2. On the first release the agent learns the protocol from the repository, confirms it
   with the owner (or interviews the owner when the method is undecided), and records it
   in the same piece of work. Later releases follow the record. The record changes only
   when the owner states a different protocol.
3. A release starts when the owner asks for one, or when the agent asks and the owner
   agrees. The owner's go covers the whole protocol for that release, production
   included. The agent stops only where a person must act (a merge: it waits for the
   owner's confirmation) and on failure.
4. It applies in personal projects, in client projects whose engagement assigns the
   release to the owner, and in the owner's own work projects. A project may record that
   the owner releases by hand. Then the agent only prepares.
5. `pakem release` stays the tool for direct-delivery services, called as protocol steps.
   No new `pakem` command is built for releases.

Considered and rejected:

- A scripted `pakem release` that reads declared settings and runs fixed steps for every
  kind. It needs a new mechanism per release kind, and the owner wants an agent that
  understands each project's release rather than a script.
- The protocol in `PROJECT_STATE.md`. It is untracked and local to one device, so agents
  elsewhere would release differently.

## Consequences

- Every project gains a `## Release` section with its first release under this skill.
  The `artifact` kind of `pakem release` stays a design. An installer's protocol uses the
  project's own packaging tool.
- `pakem project check` could later warn when a `Delivery: us` project with a release
  kind has no `## Release` section. Not built until a project needs it.
- Release evidence (version, commit, verification) stays in `PROJECT_STATE.md`.
