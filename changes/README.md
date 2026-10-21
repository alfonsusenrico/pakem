# Change fragments

Every branch records its user-visible change here as its own file instead of editing
`CHANGELOG.md`, so concurrent branches never touch the same lines.

## Rule

- One file per change: `changes/<type>-<kebab-topic>.md`, where `<type>` is one of
  `added`, `changed`, `deprecated`, `removed`, `fixed`, `security` (Keep a Changelog)
  and `<kebab-topic>` is lowercase letters, digits, and hyphens.
- Content: one or more Markdown bullet lines starting with `- `; a continuation line
  is indented two spaces directly below its bullet. Nothing else.
- Regular branches add or edit a fragment and never edit `CHANGELOG.md`. `pakem lint`
  fails on any bullet under `## [Unreleased]` and on invalid fragments.

Example `changes/fixed-install-rollback.md`:

```markdown
- `pakem install --apply` restores every written target when the manifest save fails,
  not only the last one.
```

## Release

`tools/pakem changelog` shows the pending entries grouped by type.
`tools/pakem changelog --release X.Y.Z [--date YYYY-MM-DD]` prints the section it would
insert; add `--apply` to insert `## [X.Y.Z] - DATE` under `## [Unreleased]`, delete
the consumed fragments, and write `X.Y.Z` to `VERSION`. This file is not a fragment.
