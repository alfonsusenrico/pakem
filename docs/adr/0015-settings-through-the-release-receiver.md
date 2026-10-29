# 0015. Settings through the release receiver

- Status: Accepted (owner approval 2026-10-29, with the interview flow in decision 3)
- Date: 2026-10-29

## Context

Direct delivery (ADR 0010) releases a service with one short command. Its settings do not:
area 4 (`pakem secret`) was designed in the command map but never built. On 2026-10-29 a new
personal service had to receive a bot token, an app secret, a placement file and two
repository deploy keys. The owner had to:

- SSH to the server and run `sudo -u af python3 /opt/af/af_engine.py secret-set ...` with
  the value piped from a hidden `read`;
- write `placement.env` by hand with the right owner and mode;
- generate SSH keys on the server, register their public halves on GitHub by hand, and
  shred the private files.

The first attempt failed with a "file not found" error the owner could not place. The
steps are easy to get wrong and impossible for an agent to prepare, because they need the
owner's login and sudo for every value.

The settings standard (contracts §4) already fixes where values live:

- secrets in `<base>/secrets/<NAME>`, private to the deploy user;
- placement in `<base>/placement.env` (0600);
- the names in the project's `deploy/settings.toml`.

Only the way values get there is missing.

## Decision

1. **Settings travel through the release receiver.**
   - `pakem` sends a signed settings request over the same forced-command SSH connection
     `pakem release` uses (contracts §13). The device key's `allowed_signers` principals,
     the projects it may release, are the projects whose settings it may write.
   - This adds no power. A device that may release a project can already run any code as
     the deploy user, which reads those secrets.
   - The request (`pakem.settings-request/v1`: action, project, environment, names, time,
     device, SHA-256 of the value) is signed in the `pakem-release` namespace.
   - The value follows the signed header on standard input. The receiver checks the
     digest, refuses requests older than ten minutes, and writes with the existing
     `af_settings` helpers as the deploy user.
   - Values never appear in arguments, logs, records or replies.

2. **Commands**, run in the project's repository with the host taken from `hosts.toml`:
   - `pakem secret set NAME [--environment E]`: hidden input on a terminal, or
     `--from-file F`, or `--generate KIND`. `--missing`, `--generate-missing` and
     `--dialog` work through every declared secret that is not set (decision 3).
     - `token:<bytes>` creates a random value on the host.
     - `deploy-key:read` and `deploy-key:write` create an ed25519 key pair on the host,
       keep the private key as the secret, return only the public key, and register it on
       the project's GitHub repository with `gh repo deploy-key add`. Write access is
       given only for `:write`. The title is `<project>-<environment>-<name>`.
   - `pakem secret list [--environment E]`: each declared secret with its age, or
     `missing`. Never values.
   - `pakem placement set NAME=VALUE ... [--environment E]` and `pakem placement list`.
     Placement values are host facts, not secrets. `list` prints names and whether each is
     set, and values only with `--values`.
   - Names must be declared in `deploy/settings.toml` with the matching kind. An undeclared
     or mismatched name is refused (exit `6`) before anything is sent.

3. **The agent interviews and runs the commands. Secret values never pass through it.**
   The owner asked (2026-10-29) that an agent gathers every setting the project needs and
   runs the commands itself, instead of the owner typing one command per value. The kernel
   floor forbids credentials in prompts and agent output, so the flow splits by kind.
   - `deploy/settings.toml` may describe each name, for interviews and prompts. Both tables
     are optional, and the engine ignores them:
     - `[descriptions]`: `NAME = "what it is and where to find it"`.
     - `[generate]`: `NAME = "token:<bytes>" | "deploy-key:read" | "deploy-key:write"`, for
       secrets `pakem` can create itself.
   - `pakem settings status [--environment E] [--json]` lists every declared name: its kind,
     set or missing, its description, and whether `pakem` can generate it. Values are never
     shown.
   - **Placement values** are not secret. The agent asks for them in the interview, or
     derives them from the repository and the owner's decisions, then runs
     `pakem placement set`.
   - **Generatable secrets:** the agent runs `pakem secret set --generate-missing`.
   - **Secrets only the owner knows:** the agent runs `pakem secret set --missing --dialog`.
     - On macOS (`osascript`) or a Linux desktop (`zenity` or `kdialog`), this opens one
       hidden-input dialog per missing secret on the owner's screen, titled with the name
       and its description.
     - The value goes from the dialog straight to the host. The command prints only names
       and "stored", "skipped" or "cancelled", so the value never reaches the agent's
       transcript.
     - Without a desktop, it falls back to hidden terminal prompts when a terminal is
       attached. Otherwise it reports one owner action, `pakem secret set --missing`, run
       once in the owner's terminal for all remaining secrets.
   - An agent never asks for a secret value in chat, never writes one to a file, and never
     passes one as an argument. If the owner pastes a secret into the chat, the agent stops,
     says it must be rotated, and does not use it.
   - `pakem project check` warns when a declared placement or secret name has no
     description.

4. **Checks.**
   - `pakem release` reports declared settings that are missing on the host before it
     sends a release. The engine still refuses at deploy, as now.
   - `pakem project check --host` lists missing names.

5. **Scope.** Direct-delivery hosts only. Registry and forge-variable setups keep their
   current manual steps until a project needs them. The `secrets` area starts `draft`,
   becomes `experimental` when one project's settings were all set through it, and
   `stable` after a second project.

### Rejected

- **The owner's login and sudo per value** (the `setup host` model). It means a password
  prompt per call, the deploy user's file permissions and paths that the first attempt
  tripped over, and nothing an agent can prepare.
- **Forge protected variables for direct delivery.** Secrets would travel through CI,
  which the standard forbids for pull and direct delivery (ADR 0009, contracts §4).
- **A separate settings key.** It is a second key per device with the same reach as the
  release key, so it adds management without reducing risk.

## Consequences

- The owner answers an interview and fills one dialog per secret, instead of running commands. Deploy keys are created and registered without the private
  key leaving the host.
- Existing hosts need the new receiver: the owner re-runs `pakem setup host <name>` once.
- The receiver gains a second request type, so its tests must cover forged signatures,
  stale requests, digest mismatches, undeclared names, and values never reaching logs or
  replies.
- A stolen device key can now also overwrite settings. It could already deploy code that
  does the same, so revocation (`pakem host revoke-device`) stays the response for both.
