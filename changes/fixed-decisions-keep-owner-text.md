- `pakem decide` and `pakem project adopt` refuse, changing nothing, unless the Open
  decisions markers in `AGENTS.md` appear once each, start before end, on their own lines.
  A marker quoted in prose, or a missing end marker, used to delete the owner's text.
- Pakem writes TOML strings with a real escaper: an emoji or other non-BMP character in
  an answer, a reason, a delivery note, a host path, or a project description no longer
  breaks `.pakem/decisions.toml` (and with it `decide`, `check --gaps`, `status`, and
  `release`). Adopt keeps `.pakem/project.toml` when its facts are unchanged, and keeps
  keys it does not know.
