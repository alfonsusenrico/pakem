# Antigravity Baseline Agent Instructions & Global Rules

This document establishes the permanent baseline constraints and operational rules that apply to Antigravity across ALL projects, workspaces, and sessions for this user.

---

## 1. Security & Secrets Sanitization Guardrail (MANDATORY & NON-NEGOTIABLE)

- **NEVER print, cat, read, or output sensitive credentials, secret values, tokens, API keys, private keys, or passwords in tool outputs, terminal commands, or chat responses.**
- **Inspecting Environment & Secrets Files:**
  - When inspecting `.env`, `runtime.env`, secrets files, `.ssh/`, credential stores, or any configuration files containing secrets, agents **MUST extract ONLY the variable names / keys** (e.g. using `cut -d= -f1`, `awk -F= '{print $1}'`, or regex that strips values).
  - **NEVER display, log, or echo the secret values.**
- **Handling Secrets & Configurations:**
  - If a secret or private credential is required in a configuration, CI/CD pipeline, or script, provide the variable name and clear instructions for the user to populate or set it, or use placeholders (e.g. `<YOUR_SECRET_HERE>`).
  - Never dump plaintext secrets into terminal commands, logs, artifacts, or chat transcripts.

---

## 2. CI/CD & Deploy Key Guardrail

- **NEVER use the user's personal machine SSH key (`~/.ssh/id_ed25519` or similar personal identity keys) for CI/CD, remote pipelines, or automated deployment secrets.**
- Always generate dedicated, isolated key pairs specifically labeled for the project/pipeline (e.g. `~/.ssh/<project>-deploy`), authorize that specific public key on the target server, and instruct the user or store only that dedicated deploy key in CI secrets.
