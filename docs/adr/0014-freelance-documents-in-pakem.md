# 0014. Freelance documents in `pakem`

- Status: Proposed (plan approved by the owner, 2026-10-27)
- Date: 2026-10-27

## Context

The owner's freelance documents (proposal, invoice, final report) came from a separate
app: FastAPI, Postgres, a Telegram bot, and an OpenRouter planner that drafted the text,
rendering Jinja2 templates to PDF with Playwright. The owner is retiring it and wants the
coding agent to interview them, write and update the documents, and render them ready to
send (decisions of 2026-10-27, recorded in the discovery note). The documents are short
approval documents for small engagements, so the tool must stay simple.

Constraints: the framework repository is public, so wording, issuer details, and client
records never enter it. `pakem` is standard-library Python. Records must stay outside
every code repository, so builds and zips of a repository can never carry them.

## Decision

1. **A `pakem docs` area**, stage `experimental` until a real engagement has gone through
   it. It stores everything as plain files in one private directory outside every
   repository (the owner's freelance folder), named once in
   `~/.config/pakem/documents.toml`. `pakem` refuses a store inside a git work tree. Backup is the owner's (local plus a backup for now).
2. **Store layout**:

   ```text
   documents/
     config.toml                  issuers (name, email, phone, city, bank), numbering
     wording/<type>.<lang>.md     the owner-approved fixed wording, read-only to agents
     PRJ-2026-007-<client-slug>/
       engagement.toml            client, kind, language, issuer, per-engagement facts, stage
       records.toml               approvals, payments, scope changes, handover, events
       PROP-2026-007/v1.toml      a document's content, one file per version
       PROP-2026-007/v2.toml
       PROP-2026-007/v2.pdf       the rendered version (and its .html)
   ```

3. **Engagement kinds and stages**, fewer than the generator's eleven:
   - `fixed` (fixed scope, solo): `discovery` → `proposal` → `development` → `finishing`
     → `closed`. A recorded proposal approval opens `development`. Closing needs an
     approved final report, every invoice paid or voided, and a recorded handover (a BAST
     when the engagement asks for one).
   - `workstream` (inside a client's team): `active` → `closed`. Work reports, invoices,
     receipts, and minutes, with no proposal or final report.
   - Invoices and receipts may be issued at any point after approval, because payments
     follow the proposal's milestone table.
4. **Numbering.** `PRJ-YYYY-NNN` continues from the owner's last issued number (asked once
   by `pakem docs init`). Every document shares its project's number (PROP-2026-007),
   repeating types add a running suffix (INV-2026-007-01), and the codes are those agreed
   (QUO, NDA, AGR, CR, MSR, KW, MOM, WR, WAR, BAST beside PRJ, PROP, FINAL, INV). A
   number is never reused. A voided document keeps its number.
5. **Content is data, wording is fixed.** The agent writes each version's content file
   from the interview with the owner. Fixed wording comes only from `wording/`, filled
   with the engagement's facts. An approved version is frozen: `pakem` refuses a new
   version of an approved proposal or final report, and later changes become change
   requests.
6. **Checks before rendering** (`pakem docs check`): each type's required fields are
   present, no bracketed placeholder is left, totals and outstanding balances recompute,
   referenced documents exist and are approved where they must be, and the stage allows
   the type. Amounts in words are generated, never typed.
7. **Rendering.** `pakem` writes the HTML in the approved layout D (classic header, ruled
   content), then prints it to PDF with Google's chrome-headless-shell (Chrome for
   Testing), the Chromium build made for automated printing. `pakem docs browser`
   installs it on demand after the owner's confirmation, under
   `~/.local/share/pakem/chrome-headless-shell/<version>/`, and checks it by printing a
   test page. On Linux an installed Chrome or Chromium also works, and any browser can be
   named in `documents.toml`.
8. **Records are the owner's facts.** `pakem docs approve`, `pay`, `handover`, `void`, and
   `close` record what the owner states, with the method and evidence. Agents prepare
   them from what the owner supplies and never assert an approval or a payment on their
   own (`freelance-delivery`).
9. **What lives where.** The public repository holds the engine, the D layout, and each
   type's field list. The store holds the wording, issuers, engagements, and every
   rendered file. Sending documents stays the owner's (kernel §6).

Commands, all with `--plan` and `--json` like every `pakem` area:

| Command | Does |
| --- | --- |
| `pakem docs init --last-project PRJ-2026-NNN` | Creates the store, `config.toml`, and `wording/` once |
| `pakem docs engagement new --client ... --name ... --kind fixed\|workstream --lang id\|en` | Allocates the project number and folder |
| `pakem docs new <type> <PRJ>` | Allocates the document code and writes `v1.toml` with every field to fill |
| `pakem docs check <code>` | The checks in decision 6 |
| `pakem docs render <code>` | HTML and PDF of the latest version, after the checks pass |
| `pakem docs approve\|pay\|handover\|void <code> ...` | Records an owner fact with its evidence |
| `pakem docs status [<PRJ>]` | Stage, documents, outstanding balance, and the next step |
| `pakem docs close <PRJ>` | Closes when decision 3's guards hold |

Considered and rejected:

- Keeping the generator. It needs Postgres, a bot, and a hosted model for work the coding
  agent now does in the session.
- Records inside each repository, excluded from git. Builds and zips made from a checkout
  could carry invoices and prices.
- Jinja2 and Playwright. Not standard library.
- The desktop browser already on the device (tested with Brave 1.98, Chromium 156, on
  macOS). Started headless from a terminal, it asks macOS for camera, microphone, and
  input-monitoring access on the terminal's behalf, and hangs while the prompt waits.
  Granting a terminal those permissions to print a PDF is not acceptable.
  chrome-headless-shell printed the same page in two seconds with no prompt.
- Wording in the public repository. It is the owner's text and stays private.

## Consequences

- `freelance-delivery` (profile `freelance`) points its records and documents guidance to
  `pakem docs`, and drops the proposal generator. That is a profile version bump.
- `docs/pakem-command-map.md` gains the area. Tests cover numbering, stage guards, the
  checks, and rendering with a fake browser.
- The approved wording drafts become `wording/<type>.<lang>.md` in the store on
  `pakem docs init`. They are written there by the agent from the approved drafts, not
  from the repository.
- The owner archives the proposal generator repository.
- Not built: sending, e-signature, tax calculation (a tax line is an ordinary invoice
  item), and currency conversion. IDR and USD amounts are formatted, nothing more.
