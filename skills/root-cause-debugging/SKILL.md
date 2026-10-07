---
name: root-cause-debugging
description: Use when investigating a defect, regression, flaky or intermittent failure, failed deploy, or unexplained runtime behaviour, and before attempting a second fix for the same symptom. Corrects the responsible mechanism instead of hiding a downstream symptom.
---

# Root-Cause Debugging

Debugging is controlled causal investigation, not a sequence of plausible patches.
Logs, traces, tests, code, and graphs are observations; an explanation stays a
hypothesis until evidence distinguishes it from the alternatives.

## 1. Establish the failure

- Separate observed behaviour from hypotheses.
- Record expected and observed behaviour, exact input and sequence, affected and
  unaffected cases, frequency, and the first known bad state when available.
- Build the narrowest faithful reproduction. If reproduction is impractical, state
  that limit and name the evidence that substitutes for it.
- Never describe an unconfirmed change as a fix.

## 2. Confirm the tested state

- Record repository revisions and relevant working-tree changes, built artifact or
  image identity, configuration schema, migration state, dependency versions, and
  runtime topology as applicable.
- Verify that the running artifact contains the code being evaluated. Build success
  and build logs do not prove runtime behaviour or deployment identity.
- Isolate investigation from unrelated changes with a branch or worktree based on
  the intended revision. Never discard user work for isolation.
- When known good and bad revisions exist, prefer bounded comparison or
  `git bisect` over rereading the codebase.

## 3. Trace the responsible path

- Follow one failing operation through every responsible boundary.
- At each boundary compare actual input, output, state transition, ownership,
  timeout, retry, and error contracts.
- Correlate processes with an existing safe request, job, or trace identifier.
  Never put secrets or personal data in telemetry.
- Account for stale containers, caches, persisted data, clocks, concurrency,
  retries, feature flags, environment drift, and instrumentation side effects.
- Instrumentation exposes a boundary; it is not a correction. Remove or justify it
  afterwards under normal privacy and review rules.

## 4. Manage hypotheses

- For recurrent, cross-boundary, asynchronous, or stateful failures, keep a
  hypothesis ledger ([template](references/hypothesis-ledger.md)).
- For each hypothesis record the mechanism, supporting and conflicting evidence, a
  prediction that must hold, the smallest discriminating experiment, and the
  result: confirmed, rejected, or inconclusive.
- Preserve negative results so later sessions do not repeat disproven work.
- Prefer experiments that change one variable and rule alternatives in or out.

## 5. Prove and correct the cause

- Before changing code, name the first violated contract or incorrect state, the
  component that owns it, the mechanism that produced it, and how it explains every
  downstream symptom.
- Root cause can be a conjunction of conditions; do not force a single-cause story
  or stop at the first correlation.
- Fix the responsible mechanism and contract in its owning component. Do not
  compensate downstream because that hides the symptom.
- Remove failed experimental workarounds unless separately approved, and inspect
  sibling paths that share the corrected contract.

## 6. Verify the correction

- Prove the correction against the original failure fingerprint.
- Prefer a regression test that fails for the expected reason before the fix and
  passes after it.
- Verify the owning component, the affected boundary contracts, a realistic
  integration path, and the original user-visible flow as risk requires.
- Exercise relevant negative, retry, duplicate, timeout, cancellation, restart,
  concurrency, and partial-failure cases.

## 7. Circuit breaker

- After one failed fix, stop patching and update the causal model with what the
  result disproved.
- After two failed fixes for the same symptom, make no third change until the
  failing state and artifact identities are re-established, competing hypotheses
  are reviewed, and a new discriminating experiment is defined.
- Seek an independent review of the evidence when available. A fresh opinion
  without new evidence is not progress.

## 8. Mitigation, restoration, and record

- Keep mitigation, causal correction, and cosmetic restoration separate.
- Urgent mitigation may protect users, data, security, or availability before
  diagnosis completes. Label it temporary and record risk, owner, rollback, and
  removal condition. It does not close the problem.
- For significant or recurring failures write an incident record
  ([template](references/incident-record.md)).
- Keep raw logs out of plans and handoffs; keep safe references and the facts
  needed to resume.
- In planned work, keep an unknown cause explicitly unknown: plan reproduction,
  instrumentation, and diagnosis first, update the design with the supported
  explanation, then implement. Mark a fix task done only after its regression and
  runtime evidence pass.
