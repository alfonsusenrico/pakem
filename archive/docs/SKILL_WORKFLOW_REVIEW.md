# Skill Workflow Review

Reviewed 2026-09-18. Scope: global development policy and the named skills;
no application code, project specifications, production systems, or cloud
integrations were changed.

## Verified versions and actions

| Component | Before | Result |
| --- | --- | --- |
| Caveman core skill | Installed file | SHA-256 matches the latest release, v2.7.0 |
| Ponytail core skill/plugin | 4.10.0 | Core file matches the latest release, v4.10.0 |
| OpenSpec CLI | 1.3.1 | Updated to 1.13.1; version and help verified |
| Graphify CLI | 0.8.47 | Updated through uv to 0.9.63; version verified |
| Graphify Codex skill/references | 0.8.47 | Refreshed with the native installer; previous SKILL.md backed up |

Sources: [Caveman release](https://github.com/JuliusBrussee/caveman/releases/tag/v2.7.0),
[Ponytail release](https://github.com/DietrichGebert/ponytail/releases/tag/v4.10.0),
[OpenSpec release](https://github.com/Fission-AI/OpenSpec/releases/tag/v1.13.1),
[Graphify package](https://pypi.org/project/graphifyy/0.9.63/).

The core SKILL.md files were read, along with Graphify query guidance, current
OpenSpec propose/apply/archive implementations, both review skills, the installed
frontend-design and Hallmark instructions, and the focused engineering skills.
This was not an exhaustive audit of every auxiliary command or hook in these
repositories. No local productivity benchmark was performed.

OpenSpec project-generated skills are separate from its global CLI. The sampled
Student Analysis copies still identify 1.3.1. Refresh those using native
`openspec update` when entering the project, after inspecting its instructions,
working tree, and any customizations; review the resulting diff. This review
did not regenerate files across unrelated application repositories. Graphify's
existing Claude skill was also reported as 0.8.47; only the active Codex skill
was refreshed. Do not describe all hosts' generated instructions as updated.

## Findings and decisions

1. **Ponytail protects understanding but its defaults need limits.** It explicitly
   requires tracing the flow and callers before simplifying. Its one-check
   minimum, preference for fewer files, and reduced-version suggestion cannot
   replace repository test gates, ownership boundaries, or approved scope.
   `ponytail-review` explicitly excludes correctness, security, and performance;
   it must supplement normal review.
2. **Caveman is primarily communication compression.** Its core preserves exact
   technical substance and normal prose in durable artifacts. Defaulting to
   lite retains readable grammar. Savings from shorter output cannot establish
   savings in total session cost or improved correctness. Required progress,
   uncertainty, risks, and requested explanations remain visible.
3. **Graphify is a reusable navigation aid.** Graph coverage and inferred edges
   are not proof of current runtime behavior. Verify source locations and
   callers; use direct search for known symbols. Incremental updates and bounded
   queries are preferable to repeated whole-repository extraction. An available
   provider key does not authorize uploading project material.
4. **OpenSpec preserves decisions and acceptance.** Current propose instructions
   require discovery before drafting; apply rejects silently narrowing specified
   behavior; archive verifies spec synchronization. These fit the existing
   approval workflow. Detailed plans stay detailed where decisions require it.
5. **Generated comments are not a quality requirement.** Ponytail requests debt
   tags; Hallmark requests branded CSS stamps and self-critique scores. The new
   project policy rejects these, preserving useful technical comments and
   recording significant limitations in existing specs or documentation.
6. **Instruction size matters.** The original global AGENTS.md was 36,901 bytes.
   Its new version is below 32 KiB; specialist AI runtime standards were moved
   verbatim into AI_WORKFLOW_ENGINEERING_STANDARD.md. Codex documents a default
   combined instruction limit of 32 KiB, so nested project instructions can
   still exceed the combined budget. Agents must explicitly read applicable
   project instructions and references; this is not a guarantee of automatic
   inheritance across independent Git roots.

See [Codex instruction discovery](https://developers.openai.com/codex/guides/agents-md/).

## Recommended working sequence

Read applicable project rules and active specs. Locate the affected flow with
Graphify or targeted search. Capture meaningful changes in OpenSpec and obtain
approval. Implement the complete scope using Ponytail and the relevant focused
skill. Verify behavior and required repository gates, review correctness and
complexity, then hand off evidence. Archive after acceptance. Caveman lite keeps
routine communication short throughout; it does not shorten required work.

For comparable completed changes, evaluate total input/output tokens and cost
where available, elapsed time, failed checks, regressions, review corrections,
and rework. Keep model and task differences visible. Do not install telemetry
or build a measurement service merely to claim efficiency.

## Additional skills

- **Use existing engineering skills first:** investigate-first, surgical-patch,
  safe-refactor, migration, and verify-and-stop. Select by task instead of
  stacking them. These provide evidence and stopping conditions; another generic
  clean-code skill would largely duplicate Ponytail and the new comment policy.
- **Use installed frontend-design for routine UI work.** It covers intentional
  design, real content, responsiveness, focus, reduced motion, and visual critique.
  The approved CIS design standard remains controlling; aesthetic novelty is not
  a reason to change an established product.
- **Keep Hallmark selective.** Its audit/reference workflows can be useful, but
  its full build flow adds extensive references, theme rotation, repeated
  questions, output stamps, and extra artifacts. These are a poor default for
  incremental CIS application work. Apply the project's explicit exceptions.
- **Consider Vercel web-design-guidelines for UI review.** Its skill and live
  rule source were inspected. It covers semantics, forms, focus, keyboard/touch
  interaction, motion, content overflow, and hydration. Treat stylistic rules
  and blanket performance thresholds as review prompts, not automatic reasons
  to change project conventions or add dependencies. Static review supplements
  actual browser checks. Not installed by this review.

The [Vercel skill](https://skills.sh/vercel-labs/agent-skills/web-design-guidelines)
showed approximately 644K installs, and its official
[source repository](https://github.com/vercel-labs/agent-skills) approximately
31K stars at review time. Popularity is provenance context, not correctness proof.
Optional installation: `npx skills add vercel-labs/agent-skills@web-design-guidelines -g -y`.
Its [review criteria](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md)
should be checked against the project's requirements when used.

## Maintenance

The global AGENTS.md owns the policy. This document records the review evidence;
it need not load during ordinary coding. Preserve upstream installations and
keep project exceptions in the project rules. Refresh generated instructions
deliberately after upgrades; do not make toolchain maintenance a prerequisite
for every feature session.
