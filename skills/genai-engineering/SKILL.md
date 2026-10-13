---
name: genai-engineering
description: Use when designing, implementing, reviewing, or changing features that use a language model - prompts, structured output, RAG, tool-using agents, evals and benchmarks, model or provider changes, LLM security, cost and latency budgets, or AI observability.
---

# GenAI Engineering

Applies to projects with the `llm` capability. Agent architecture (plain tool loop,
LangGraph, another framework) is a project decision; these rules hold for any of them.

## 1. Always

- **Prompts are versioned files**, never inline strings: one file per prompt with an
  id, version, and change note. Follow [prompts](references/prompts.md).
- **Explicit model per call**: provider and model id are passed explicitly, never
  left to a library default; record them with each result.
- **Evals before shipping changes**: any change of model, prompt, provider, or
  generation parameters ships only after the eval suite is re-run and compared with
  the current baseline. Follow [evals](references/evals.md).
- **No content in logs**: prompts, retrieved documents, and model output are never
  logged; log ids, token counts, latency, model, outcome category.
- **Budgets**: token, cost, latency, and payload limits are enforced before sending.
  Follow [cost and latency](references/cost-latency.md).
- **Security**: apply [LLM security](references/security.md) controls.

## 2. When code consumes the model's output

(records, routing decisions, numbers, UI fields)

- Request strict structured output (JSON schema), validate it in code, allow bounded
  repair or retry, then fail closed with a clear outcome.
- Deterministic code computes facts (scores, totals, matches, permissions); the model
  interprets supplied evidence only. Follow [structured output](references/structured-output.md).

## 3. When a model decision has consequential side effects

(money, messages to people, changes to records)

- A confidence gate: below the threshold, a human confirms before the action.
- Dry-run mode for new pipelines until results are reviewed.
- Record which model, prompt version, and confidence produced each action.

## 4. Retrieval and agents

- Retrieval: follow [RAG](references/rag.md).
- Tool-using agents: follow [agent patterns](references/agent-patterns.md) - typed
  tools, guards enforced in code, iteration caps, least privilege.

## 5. Observability

Follow [observability](references/observability.md): content-free traces and metrics
by default; any content capture needs a written decision.

## 6. Presentation

AI output shown to users is recognisably the model's, states the basis of any facts
it presents, and follows the project's own design (`ui-ux-quality`). Never display or
store hidden reasoning.
