# 0017. Projects follow Pakem updates by themselves

- Status: Proposed (plan approved by the owner, 2026-11-03)
- Date: 2026-11-03

## Context

After `pakem update`, an agent in a project learned only that the kernel and skills
changed: the session start said "Pakem 2.2.0 is up to date", and a running session was
told which files to re-read. Nothing told it which framework changes needed work in its
project, which of them it had already done, or that its copies of Pakem's CI workflows and
deploy scripts were out of date. The version also stays the same between releases (25
changes since 2.2.0 on 2026-11-03). So the owner wrote handoffs by hand after each update,
and projects drifted.

On 2026-11-03 a review of every project found it: no personal project pinned a profile,
two still carried the retired `default:no-billed`, brand-studio ran the previous
`security.yml`, and three projects held older copies of `deploy/af` scripts. Thirteen
handoffs were written by hand for one round of CI changes. The owner asked that agents
learn of updates, and what to do after them, without that.

## Decision

1. **Copies of Pakem files are checked mechanically.** `pakem project check` finds the
   files a project copied from Pakem: workflows that start with the template line
   `# Pakem CI template:` or carry a `Copied from Pakem` line, and `deploy/af/*`. It
   compares each, without its provenance line, with every version of its asset in the
   framework checkout's git history: the current one (nothing to do), an older one (an
   unmodified copy, which `pakem project sync --templates --apply` re-copies), or none (a
   copy the project changed, which its agent merges by hand). A workflow that only shares
   a file name with a template, such as a `dist`-generated `release.yml`, is not a copy.
2. **Change notes say what projects must do.** A change fragment ends with a
   `- Projects: ...` bullet when projects must act on it. `pakem lint` checks its form.
3. **Each project records what it aligned to.** `pakem project sync --aligned --apply`
   writes `.pakem/framework.toml`: the Pakem commit, and for each changed copy the Pakem
   version it was merged with, so that copy stays quiet until Pakem changes it again.
   `pakem project check` lists the notes of fragments merged after that commit, read from
   git so they survive a release. Without a record, the project never aligned, so it lists
   every note in the history (amended 2026-11-03: the pending fragments alone lost every
   note at a release).
4. **Agents notice by themselves.** The session start hook adds one line while follow-ups
   wait ("Pakem changes to align here: ..."). The `pakem-update` skill works through them:
   own projects on a topic branch, work projects proposed, client projects noted. Then it
   records the alignment.
5. **The owner sees which projects need a session.** `pakem update` ends with the known
   projects (the weekly report's repositories and the hosts' project folders) that have
   follow-ups.

## Consequences

- No handoff is needed for a framework change that a note or a copy check covers. A
  project-specific judgment ("is this CI safe to run twice at once?") still belongs to the
  project's agent: the tool only makes sure no agent misses the note.
- Authority is unchanged. Agents commit on topic branches and push only after the owner
  accepts the work. Merging a changed copy and acting on a note are work like any other.
- A framework root that is not a git checkout has no follow-ups. Notes need their exact
  form, which lint checks.
- Projects that only fix their own CI (not copies of Pakem files) receive the notes but no
  copy checks.
- Considered and rejected: provenance headers in every copy (existing copies lack them, and
  history matching works on what is there), and a version bump per change (releases stay
  batched, ADR 0011).
