# Incident Record

Status: Active
Applies to: significant or recurring failures
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner baseline debugging guide (2026-09)

Blameless. Store under the project's docs (for example `docs/incidents/YYYY-MM-DD-<slug>.md`).

- **Summary**: one paragraph a newcomer understands.
- **Impact**: who and what was affected, for how long.
- **Trigger**: the event that started it.
- **Mechanism**: the first violated contract, its owning component, and how it
  produced each symptom.
- **Contributing conditions**: configuration, load, timing, data, or process.
- **Detection gap**: why it was not caught earlier.
- **Test gap**: which test would have failed, and whether it now exists.
- **Failed approaches**: what was tried and what each disproved.
- **Corrective action**: the change, with revision and evidence.
- **Mitigation still in place**: owner, risk, rollback, removal condition.
- **Prevention evidence**: the regression test, alert, or rule that now guards it.
- **Rule promotion**: whether a durable lesson moves into `AGENTS.md`, a spec, or
  the framework (only if it is general and not discoverable from code).
