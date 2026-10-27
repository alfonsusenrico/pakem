# Release Protocol

Status: Active
Applies to: every release of a project with `Delivery: us`
Last reviewed: 2026-10-27
Review by: 2027-04-27
Sources: owner decisions 2026-10-27 (ADR 0013); the owner's service and installer releases

Each project keeps its release protocol in a `## Release` section of its `AGENTS.md`.
The agent learns it on the first release, writes it there, and follows it on every
release after, so every agent on every device releases the same way. `PROJECT_STATE.md`
is never its home: it is local to one device.

## 1. When a release starts

- The owner asks for a release, or merged work is ready and the agent asks whether to
  release. Never start one without the owner's yes.
- The owner's go covers the whole protocol for that release, production included. Stop
  only where a person must act (a merge: say what to merge, wait for the owner to confirm
  it merged, then continue) and on any failure.
- When the section says the owner releases by hand, prepare the version, notes, and
  checks, report them, and stop.
- `Delivery: others`: there is no protocol of ours. Keep CI green and stop at the merge
  request.

## 2. Learning the protocol (no `## Release` yet)

Read the repository before asking, and separate what it proves from what you infer:

- previous tags and their scheme: `git tag --sort=-v:refname`, pre-release suffixes
  such as `-rc.1`;
- release automation: workflows triggered by tags or releases (`.github/workflows/`,
  `.gitlab-ci.yml` rules), `deploy/release.toml` (a `pakem release` service), packaging
  configuration (for example cargo-dist, GoReleaser, electron-builder, Gradle);
- version files (`Cargo.toml`, `package.json`, `pyproject.toml`, `VERSION`) and the
  changelog's format, or release notes on the forge;
- release scripts, docs, and the history of earlier release commits.

Then show the owner the protocol you found, in one message, with each step's evidence,
and ask only what the repository cannot answer. When the release method is not decided
yet (a first release, a new kind), interview the owner until the record's fields are
settled, offering options with a recommendation.

## 3. The record

```markdown
## Release

- Who: the agent on the owner's go | the owner by hand (agent prepares only)
- Kind: service | installer | package | library | none
- Trigger: what starts it (a tag push, `pakem release promote`, a CI job)
- Versions: the scheme the previous tags follow, and when to use a pre-release suffix
- Notes: where release notes go (CHANGELOG section, forge release) and their format
- Before: the checks that must pass on the exact commit, and the version files to bump
- Steps: the ordered commands, exact
- Staging and soak: where it goes first and how long it must stay healthy, or none
- Verify: what proves the release (revision on the health URL, assets on the release page)
- Rollback: how to return to the previous release
```

Write the section in the same piece of work as the first release, on a docs branch, and
give it to the owner to merge. Change it only when the owner states a different
protocol, and update it in the same change that applies the difference. Evidence of each
release (version, commit, verification) goes to `PROJECT_STATE.md`, not the record.

## 4. Running it

1. Start from `main`, clean and equal to `origin/main`, with the checks the record names
   passed on that exact commit (CI, or the project's local check script).
2. Choose the version by the project's scheme: the change since the previous tag sets the
   semver step, and the record says when a pre-release comes first. Never reuse or
   move a published tag.
3. Write the notes from the changes merged since the previous tag, in the project's
   format. When version files or the changelog must change on `main`, open a
   release-prep merge request, tell the owner, and wait for the merge.
4. Run the steps exactly as recorded. A service on direct delivery uses `pakem release`
   (the `pakem` skill, section 6), never hand-run equivalents.
5. Wait out the soak, then verify as recorded, and report the version, commit, and
   evidence.

## 5. When it fails

Stop at the first failed step. Follow the recorded rollback when production is affected,
report the exact output, and never improvise another production step. Deleting a tag or
a forge release, or re-running a failed publish, needs the owner's instruction for that
action.
