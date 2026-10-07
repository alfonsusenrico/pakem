# Sensitive and Production Operations

Read this guide before work involving credentials, sensitive data, permissions,
production, deployment, migrations, or destructive operations.

## Secrets and sensitive data

- Never expose credentials, tokens, API keys, passwords, private keys, or sensitive
  personal data in prompts, terminal output, logs, patches, or reports.
- Do not display secret-file contents or dump environment or configuration values.
- Prefer configuration schemas and sanitized examples when checking required names.
- If secret-bearing files must be inspected, use a format-aware keys-only method
  that emits validated names and never values, unmatched lines, or multiline
  continuations. Do not use `cut -d= -f1` as a sanitizer.
- Do not source an untrusted environment file for inspection.
- Applications may consume authorized secrets through their normal runtime
  mechanism; agent investigation must not reveal those values.
- Treat customer data, uploads, embeddings, traces, and metadata as possible
  disclosure channels. Define authorization, redaction, destination, and retention
  before enabling external collection.

## Authority and least privilege

- Distinguish authority to inspect, modify, deploy, migrate, publish, and delete.
  Permission for one does not authorize the others.
- Use least privilege and existing approved access paths.
- A configured connection, available credential, skill, or API key is not itself
  authorization to use it.
- Do not bypass sandbox controls, change permissions, or weaken security checks to
  make a failing operation pass.

## Production and release

- Never connect directly to or act on production without explicit user instruction.
- Reproduce and correct issues locally when practical, then deploy through the
  project's approved pipeline or release procedure.
- Do not manually edit production code or environment files or perform ad hoc
  schema changes.
- Associate release and runtime evidence with the exact deployed revision and
  artifact identity.
- Report unresolved risks before publication, deployment, or migration.

## Migrations and compatibility

- Use versioned migrations.
- Define compatibility windows, ordering, recovery, and rollback.
- Account for mixed-version operation, retries, concurrency, restarts, and partial
  failure when they affect correctness.
- Verify data preservation and required negative paths before describing the
  migration as complete.

## Destructive operations

- Confirm that deletion or overwrite is within the requested scope.
- Resolve and inspect exact targets before acting. Do not rely on unresolved
  variables, broad globs, or command substitutions to identify them.
- Never use a home directory, workspace root, filesystem root, or another broad
  collection as a recursive or destructive target.
- Prefer recoverable operations and preserve a recovery path when practical.
- Obtain required authorization when scope or recoverability is uncertain.
- After removing material data, report exactly what was removed and whether it can
  be recovered.
