# Root-Cause Debugging

Read this guide for a defect, regression, intermittent failure, or unexplained
runtime behavior. Its purpose is to correct the responsible mechanism rather than
hide a downstream symptom.

## Establish the failure

- Separate observed behavior from hypotheses.
- Record expected and observed behavior, exact input and sequence, affected and
  unaffected cases, frequency, and the first known bad state when available.
- Create the narrowest faithful reproduction. If reproduction is impractical,
  state that limit and identify the evidence substituting for it.
- Do not describe an unconfirmed change as a fix.

## Confirm the tested state

- Record repository revisions and relevant working-tree changes, built artifact or
  image identity, configuration schema, migration state, dependency versions, and
  runtime topology as applicable.
- Verify that the running artifact contains the code being evaluated. Build success
  and build logs do not prove runtime behavior or deployment identity.
- Isolate investigation from unrelated changes when practical using a branch or
  worktree based on the intended revision. Never discard user work for isolation.
- When known good and bad revisions exist, prefer bounded comparison or automated
  bisection over repeatedly rereading the entire codebase.

## Trace the responsible path

- Follow one failing operation through every responsible boundary.
- At each boundary, compare actual input, output, state transition, ownership,
  timeout, retry, and error contracts.
- Correlate processes using an existing safe request, job, or trace identifier. Do
  not place secrets or sensitive user data in telemetry.
- Account for stale containers, caches, persisted data, clocks, concurrency,
  retries, feature flags, environment drift, and instrumentation side effects.
- Instrumentation may expose a boundary but is not itself a behavioral correction;
  apply normal privacy, verification, and cleanup requirements.

## Manage hypotheses

- For recurrent, cross-boundary, asynchronous, stateful, or otherwise non-trivial
  failures, maintain a compact hypothesis ledger.
- For each hypothesis, record the proposed mechanism, supporting and conflicting
  evidence, a prediction that must be true, the smallest discriminating experiment,
  and the confirmed, rejected, or inconclusive result.
- Preserve negative results so later sessions do not repeat disproven work.
- Prefer experiments that change one relevant variable and can rule alternatives
  in or out.

## Prove and correct the cause

- Before corrective implementation, identify the first violated contract or
  incorrect state, the component that owns it, the mechanism that produced it, and
  how it explains the downstream symptoms.
- Root cause may be a conjunction of conditions. Do not force a single-cause story
  or stop at the first correlated observation.
- Fix the responsible mechanism and contract. Do not compensate in downstream
  layers merely because that hides the symptom.
- Remove failed experimental workarounds unless separately approved and inspect
  sibling paths that share the corrected contract.

## Verify the correction

- Prove the correction against the original failure fingerprint.
- Prefer regression evidence that fails for the expected reason before the fix and
  passes afterward.
- Verify the owning component, affected boundary contracts, realistic integration
  path, and original user-visible flow as risk requires.
- Exercise relevant negative, retry, duplicate, timeout, cancellation, restart,
  concurrency, and partial-failure cases.

## Anti-loop circuit breaker

- After one attempted fix fails, stop patching long enough to update the causal
  model and record what the result disproved.
- After two failed fixes for the same symptom, make no third corrective change
  until the failing state and artifact identities are re-established, competing
  hypotheses are reviewed, and a new discriminating experiment is defined.
- Seek independent evidence review when available. A fresh opinion without new
  evidence is not enough.

## Mitigation, restoration, and record

- Separate mitigation, causal correction, and cosmetic restoration.
- Urgent mitigation may protect users, data, security, or availability before full
  diagnosis, but label it temporary and record risk, owner, rollback, and removal
  condition. Do not close the underlying problem.
- For significant or recurring failures, record trigger, mechanism, contributing
  conditions, impact, detection gap, test gap, failed approaches, corrective action,
  and prevention evidence.
- Keep raw logs outside concise plans and handoffs; retain safe references and the
  facts required to resume.
- In OpenSpec work, keep an unknown cause explicitly unknown. Plan reproduction,
  instrumentation, and diagnosis first; update design and tasks with the supported
  explanation before implementing correction. Complete a fix task only after its
  specified regression and runtime evidence pass.
