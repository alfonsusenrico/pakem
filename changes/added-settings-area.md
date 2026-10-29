- `pakem secret set|list`, `pakem placement set|list`, and `pakem settings status` write and
  list a direct-delivery service's secrets and placement through the release receiver, in a
  signed `pakem.settings-request/v1` (ADR 0015, stage `draft`). `secret set` reads hidden
  terminal input or a file, or creates the value on the host (`--generate token:<bytes>`,
  or a deploy key the host makes and `gh` registers on the repository); `--missing --dialog`
  asks for each missing secret in a hidden-input desktop dialog, and `--generate-missing`
  creates what `[generate]` in `deploy/settings.toml` names. Hosts need `pakem setup host`
  once for the new receiver.
- `deploy/settings.toml` takes optional `[descriptions]` and `[generate]` tables. `pakem
  release` names required settings the host lacks before it sends, and `pakem project check`
  warns about placement and secret names without a description.
