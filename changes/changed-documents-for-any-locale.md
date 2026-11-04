- `pakem docs` works outside Indonesia. `engagement new --currency` takes any ISO 4217 code
  (EUR, JPY, ...), written as the code and the amount unless the language has a sign for it
  (`Rp` in Indonesian). The duty stamp on receipts is a rule per currency, the rupiah's
  under UU 10/2020, instead of a check written for IDR. The store's `config.toml` may set
  `paper = "Letter"` (default A4). Adding a language is one entry in each per-language table,
  and a test names any table a language lacks.
