# Service starter files

Status: Active
Applies to: service projects created or adopted with `pakem project init` / `adopt`
Last reviewed: 2026-10-22
Review by: 2027-04-22
Sources: deployment contracts v1 (release-delivery skill), ADR 0010, ADR 0011

`pakem project init` and `pakem project adopt` write these files into a service project
that we release (`Delivery: us`), filling `{{name}}` and the environments. They follow the
[deployment contracts](../../../release-delivery/references/deployment-contracts.md).
Files that still carry the `pakem:stub` marker are starters the project must replace
(its own entry point, backup, health endpoint); `pakem project check` reports them, and
the gaps (`pakem project check --gaps`) say what each answer must prove.
