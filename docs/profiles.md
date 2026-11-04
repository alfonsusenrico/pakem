# Writing a Pakem profile

Pakem's kernel and skills hold what is true in every repository. A **profile** holds what
is true in one context: your own projects, a company you work for, or client work. It adds
rules on top of the core and never weakens a floor (kernel section 2). Each project pins
one context profile, plus any organization or client profile, in its `AGENTS.md`.

Start from [`profiles/example`](../profiles/example), a neutral profile for a small team.
It is the only profile in this repository: the maintainer's own live in a private
repository, as yours should when they name your hosts, services, or people.

## What belongs in a profile

Facts and choices that hold for every project in the context, and that an agent can't
find in the code:

- **Forge and CI:** where repositories live, and whether private ones run checks on the
  forge's runners or on your own (each project still records `ci_runner`).
- **Spending:** which paid services are approved, and how to propose another.
- **Services the projects share:** an AI gateway, a single sign-on, a design system.
- **Review and merging:** who reviews and merges (the kernel says only that the agent never
  does).
- **Identity:** the name and email commits use, and which credentials automation may use.
- **Language:** the language of code, docs, and commits, and where a project states its
  UI language.

Not in a profile:
- **Universal practice** belongs in the core. Propose it upstream with `pakem propose`.
- **One project's facts** belong in that project's `AGENTS.md` or `.pakem/`.
- **Secrets** belong nowhere in a profile: a profile is plain text and is pinned into
  every project's `AGENTS.md`.

## Files

| File | Purpose |
| --- | --- |
| `profile.toml` | `name`, `version`, `requires_framework` (for example `">=2.0,<4.0"`), `digest`, and optionally `skills_dir` |
| `digest.md` | The rules, at most 2,000 characters (warning above 1,800). Pinned into each project's `AGENTS.md`; repeat no kernel line. |
| `skills/<name>/SKILL.md` | Optional skills in the Agent Skills format, installed next to the core ones. |
| `AGENTS.md` | Rules for changing the profile itself. |
| `VERSION`, `CHANGELOG.md` | The profile's version and history; bump `VERSION` and `profile.toml` together. |
| `docs/adr/` | Optional decisions with trade-offs. |

## Labelled defaults

The core marks its opinions with labels such as `default:commits` or `default:postgres`. A
project changes one with a bullet in its `## Overrides`, naming the label and the reason.
A profile may define labels of its own: write `` `default:<label>` `` next to the rule in
`digest.md`, and projects pinning the profile can override it the same way
(`pakem project check` accepts the labels that the kernel, the core skills, and the pinned
profiles define).

## Using it

1. Check it: `pakem lint --profile <dir>` (0 errors).
2. Install it on each device, next to the core: `pakem install --profile <dir> --apply`.
   Pass every profile you use; `pakem update` keeps the same ones.
3. Pin it in a project: `pakem project sync <repo> --profile <dir> --apply`. The digest is
   rendered into the project's `AGENTS.md` with its version and checksum, and
   `pakem project check` warns when it falls behind.

Keep your profile in its own repository, and keep it private when it names your hosts,
services, or people.
