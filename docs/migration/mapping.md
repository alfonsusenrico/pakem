# Migration Mapping (audit trail)

Where each legacy source went. Legacy files are preserved verbatim in `archive/`.

| Legacy source | New home |
| --- | --- |
| Workspace baseline `AGENTS.md` (2026-09) | condensed into `kernel/KERNEL.md`; procedures into skills; tool policy into `instruction-maintenance/references/agent-tooling.md` |
| `agent-guides/debugging-root-cause.md` | `skills/root-cause-debugging` (+ hypothesis ledger, incident record) |
| `agent-guides/openspec-workflow.md` | `skills/change-planning` section 5 |
| `agent-guides/sensitive-production-operations.md` | `skills/sensitive-operations` (+ `env_keys.py` replacing the text-splitting advice for env files) |
| `agent-guides/ui-ux-development.md` | `skills/ui-ux-quality` (+ accessibility-verification reference) |
| Home `AGENTS.md` (Antigravity) | deploy-identity rule into kernel floors and `sensitive-operations`; text-splitting advice for env files dropped as unsafe |
| Personal workspace `AGENTS.md` | deploy-identity rule merged; file to become a short personal workspace map |
| `docs/UI_UX_DESIGN_STANDARD.md` | archived (universal style conflicted with the baseline); design base now from the owner's approved portfolio |
| `docs/SKILL_WORKFLOW_REVIEW.md` | archived; still-valid points in `agent-tooling.md` |
| `docs/AI_PROJECT_DELIVERY_STANDARD.md`, `docs/AI_WORKFLOW_ENGINEERING_STANDARD.md` | generic parts into `change-planning`, `project-bootstrap`, `genai-engineering`, `release-delivery`; organization-specific parts go to that organization's profile repository |
| Deferred RTCFC prompting standard | generic RTCFC + EARS structure in `genai-engineering/references/prompts.md` |
| Discovery evidence and interview (2026-10-07) | `docs/working-standard-profile.md` |
