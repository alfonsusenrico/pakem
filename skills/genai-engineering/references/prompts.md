# Prompts

Status: Active
Applies to: every prompt sent to a language model
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner prompting standard draft (RTCFC, deferred 2026); owner prompts using EARS rules (2026); EARS (Mavin et al., Easy Approach to Requirements Syntax); Google prompt design guidance

## Files and versions
- One Markdown file per prompt under the project's `prompts/` directory, with a header:
  `id`, `version`, `purpose`, `inputs`, `output contract`, `changelog`.
- Changing a prompt bumps its version, records the reason, and re-runs evals.
- Prompts are loaded by id and version; results record both.

## Structure (RTCFC baseline, adapted per prompt)
1. **Role and objective** - who the model acts as and the single goal.
2. **Task instructions** - steps or rules, in order.
3. **Context** - the supplied data, clearly delimited; the model uses only this.
4. **Format** - the exact output contract (schema or layout).
5. **Constraints** - boundaries, refusals, language, tone, length.
Optional: examples (only for genuinely ambiguous decisions), recap of critical rules.
Do not force every section into every prompt.

## Behavioural rules in EARS form
- Ubiquitous: "The assistant SHALL ..."
- Event-driven: "WHEN <trigger>, the assistant SHALL ..."
- State-driven: "WHILE <state>, the assistant SHALL ..."
- Unwanted behaviour: "IF <condition>, THEN the assistant SHALL ..."
Keep persona and style text readable prose; use EARS for observable behaviour,
edge conditions, and recovery.

## Rules
- Separate trusted instructions from untrusted data (user input, documents, tool
  results); tell the model that data is not instructions.
- Never request, display, log, or persist hidden chain-of-thought; evaluate final
  outputs and observable decisions.
- Output language follows the audience; identifiers and field names stay stable.
