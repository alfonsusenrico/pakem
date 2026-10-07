# Cost and Latency

Status: Active
Applies to: every LLM feature
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner gateway and admission work (2026); provider pricing pages (check current)

- Estimate tokens and payload size before sending; reject or trim inputs that exceed
  the budget with margin below the provider or gateway limit.
- Set `max_output_tokens` explicitly per call.
- Track cost per call from usage and a versioned pricing table with its source and
  effective date.
- Cache stable prompt prefixes where the provider supports it; record cache use.
- Stream long responses to users; set idle and total timeouts.
- Distinguish provider rate limits, gateway admission, and model failures in metrics.
- Choose the smallest model that passes the evals; record the decision and its eval
  evidence.
