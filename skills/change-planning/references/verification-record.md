# Verification Record

Status: Active
Applies to: every OpenSpec change (`openspec/changes/<change>/verification.md`)
Last reviewed: 2026-10-07
Review by: 2027-04-07
Sources: owner verification records (2026-09 to 2026-10)

```markdown
# Verification: <change>

- Revision: <branch> @ <commit> (working tree clean | listed changes)
- Date and environment: <local / CI / staging>, disposable data: <how created>

## Checks
| Check | Command or method | Result |
| --- | --- | --- |
| Unit and integration tests | `<command>` | <passed N / failed N> |
| Type check, lint, format | `<command>` | |
| HTTP or browser flows | `scripts/verify/<name>` | |
| UI widths / 200% text / keyboard / axe | | |

## Evidence
- Outputs saved under `research/evidence/<change>/`

## Not verified
- <item> - <reason and who verifies it>

## Awaiting owner confirmation
- <item> (also recorded in PROJECT_STATE.md)
```
