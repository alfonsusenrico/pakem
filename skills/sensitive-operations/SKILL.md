---
name: sensitive-operations
description: Use before touching secrets, .env or credential files, deploy keys, permissions, production systems, deployments, database migrations, or deleting or overwriting data or files. Defines authority, keys-only secret inspection, migration safety, and destructive-target checks.
---

# Sensitive and Production Operations

## 1. Authority

- Inspecting, modifying, deploying, migrating, publishing, and deleting are separate
  authorities. Permission for one does not grant the others.
- A configured connection, available credential, skill, or API key is not itself
  authorization to use it.
- Use least privilege and the project's existing approved access paths.
- Never bypass sandboxes, permissions, hooks, or security checks to make a failing
  operation pass.

## 2. Secrets and sensitive data

- Never print, log, commit, or paste credentials, tokens, API keys, passwords,
  private keys, or sensitive personal data.
- To learn which variables a secret-bearing file defines, run the keys-only reader:
  `python3 scripts/env_keys.py <file>` or
  `python3 scripts/env_keys.py .env --compare .env.example`.
  It prints validated names only and never values, comments, unparsable lines, or
  multiline continuations. Do not improvise text tools for this.
- Prefer configuration schemas and `.env.example` when checking required names.
- Never source or execute an environment file to inspect it.
- If a secret is exposed (printed, logged, committed, or sent to a model), stop,
  tell the owner which secret and where, and recommend rotation. Do not repeat it.
- Applications consume secrets through their normal runtime mechanism; agent
  investigation must not reveal them.
- Treat user data, uploads, embeddings, traces, prompts, and model output as
  disclosure channels. Follow [logging and privacy](references/logging-privacy.md).

## 3. Deploy and automation identity

- Never use a personal machine identity (for example `~/.ssh/id_ed25519`) for CI,
  pipelines, or automated deployment.
- Create a dedicated key pair per project and purpose (for example
  `~/.ssh/<project>-deploy`), authorize only its public key on the target with the
  minimum access, and store the private key only in the CI secret store.
- Validate keys only through safe metadata: path, ownership, permissions,
  fingerprint, public key. Never print private-key contents.

## 4. Production and release

- Never connect to or act on production without the owner's explicit instruction
  for that action.
- Reproduce and fix locally when practical, then release through the approved
  pipeline. No manual edits to production code, environment files, or schema.
- Tie release and runtime evidence to the exact deployed revision and artifact.
- Report unresolved risks before publishing, deploying, or migrating.

## 5. Migrations

Follow [migration safety](references/migrations.md): versioned migrations,
compatibility windows, ordering, recovery and rollback, mixed-version operation,
and data-preservation evidence before calling a migration complete.

## 6. Destructive operations

- Confirm deletion or overwrite is within the requested scope.
- Resolve and inspect exact targets first. Never rely on unresolved variables, broad
  globs, or command substitution to identify them.
- Never target a home directory, workspace root, filesystem root, or other broad
  collection recursively.
- Prefer recoverable operations (move to a backup, soft delete) and keep a
  recovery path.
- Ask before deleting data or files the current task did not create, and whenever
  scope or recoverability is uncertain.
- Afterwards, report exactly what was removed and whether it can be recovered.
