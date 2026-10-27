- `pakem docs` (experimental, ADR 0014): freelance engagement documents kept as plain
  files in a private store outside every repository. The agent writes each document from
  the owner's answers, `check` validates it (no placeholders, totals, references, stage),
  `render` prints it in layout D to PDF with chrome-headless-shell (installed on demand by
  `pakem docs browser` after the owner's confirmation),
  and `approve`, `pay`, `handover`, `void`, and `close` record the owner's facts. Fourteen
  document types, numbering that continues from the owner's last project, and the
  `freelance` profile's `freelance-delivery` skill now uses it (profile 1.1.0).
