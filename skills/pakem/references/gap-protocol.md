# Gap protocol

Status: Active
Applies to: agents filling a project's gaps (`pakem project check --gaps`, `pakem decide`)
Last reviewed: 2026-10-23
Review by: 2027-04-23
Sources: ADR 0011 decisions 2, 3, and 6; docs/pakem-command-map.md in the framework

A gap is a project- or case-specific decision an `pakem` command leaves open. `pakem project
check --gaps --json` lists them:

```json
{"id": "health.proof", "required": true,
 "question": "What must /health prove before a release counts as working?",
 "why": "The engine restores the previous release when health fails.",
 "options": ["process up", "process up and database answers", "custom"],
 "default": "process up and database answers",
 "target": "deploy/release.toml [health] + docs/deployment.md",
 "done_when": "release.toml health block validates and the endpoint exists in code",
 "blocks": ["release"]}
```

The agent fills gaps in this order, and stops when the finish condition holds:

1. Run `pakem project check --gaps --json`. The list is fixed for this session. Do not add
   questions, except a blocker no gap covers; record that as a new gap.
2. Answer what the repository already proves (code, docs, earlier decisions in
   `AGENTS.md`) without asking. Say which gaps were answered this way. When an answer
   names a behaviour, read the code that has to deliver it: for `health.proof`, open the
   handler at the `[health] path` and confirm it checks what the answer claims (a
   "database answers" health path must query the database). A passing check proves only
   that the files exist, not that they do what the answer says.
3. Ask the rest in **one message**, grouped by topic, each with its default. "Use the
   defaults" is a complete answer.
4. Write each answer to its `target`, with a one-line reason where the target is
   `AGENTS.md` or a decision note.
5. Run `pakem project check --gaps`. Ask a follow-up only for answers that failed validation or
   contradict each other, in one message.
6. **Finish:** every required gap is answered, provisional (with a revisit trigger), or
   pending (with what is missing, from whom, and a date to ask again), each recorded with
   its reason. After the second round, record anything still unclear as pending; never
   start a third round.

Pending gaps are not asked again until they are due: their date passed, or the work
reached a stage they block; then they come up at session start as one question. Each gap
declares the stages it blocks, so agents work on everything else meanwhile and keep the
pending choice open in the design.

Optional gaps take their default unless the developer objects; they are listed once, not
asked one by one.

Record each answer with `pakem decide <gap> <answer> --reason ...`, a working assumption with
`--provisional <assumption> --revisit <trigger>`, and something that waits on others with
`--pending <what> --from <who> --ask-again <date>`. A blocker no gap covers gets
`--question <text> --blocks <stages>`.
