# 0012. Kernel, profile, and project boundaries

- Status: Proposed
- Date: 2026-10-27

## Context

The kernel is installed globally (ADR 0007), so every repository on a device loads it:
the owner's personal projects, the organization's work projects, and client
repositories. Profiles reach a repository only through the digest block that
`pakem project sync --profile` writes into its `AGENTS.md`.

On 2026-10-27 an agent in a work project with the `llm` capability wrote an invented
monthly budget into its `AGENTS.md` `## Overrides`, attributed to the owner, to clear the
`pakem project check` warning for `default:no-billed`. In that organization, model calls
go through the organization's AI gateway, are paid by the organization, and are metered
centrally. No project budget exists. The agent named the kernel as its source.

Root causes:

1. `default:no-billed` came from a personal-account incident (ADR 0010: hosted runner
   minutes on the owner's private GitHub repositories), but it was placed in the kernel,
   which work and client repositories load too.
2. The owner's personal projects pin no profile. `profiles/personal` holds freelance and
   client-engagement rules only. So the kernel was the only place for a rule meant for
   the owner's own projects.
3. ADR 0001 keeps organization content out of the kernel, but nothing keeps the owner's
   personal preferences out.
4. `project check` (`_check_billing`) and the `gateway.profile` gap demand a budget from
   every `llm` project. When no budget exists, the only way to satisfy them is to invent
   one.

An audit of every kernel line, with the owner's answers (2026-10-27), found three
more lines that hold in only one context: the credential split per device and per
builder (Pakem's own release design), the `pakem propose` sentence (how the framework
changes), and "releases use `pakem`" (work projects release through the organization's
GitLab tag flow, and client projects through the client's platform). It also found one
line that holds only for repositories the owner creates: formatter, linter, and CI from
the first commit.

## Decision

1. **Three layers, each with an admission test.**
   - Kernel: only rules that hold in personal, work, and client repositories alike.
   - Profile: rules for one context. A project pins exactly one context profile
     (`personal`, `freelance`, or the organization's work profile), plus any
     organization or client profile. The organization's own profile serves as the work
     profile, with no separate generic work layer.
   - Project `AGENTS.md`: facts, budgets, client conventions, and who reviews and merges.
   `instruction-maintenance` gains the test: a kernel rule must hold in all three
   contexts, or it belongs in a profile.
2. **Every project pins its profile**, the owner's personal projects included.
   `profiles/personal` splits into `personal` (the owner's own projects) and `freelance`
   (client engagements). Both stay in this repository (ADR 0001 amendment).
3. **Profiles may change labelled defaults.** A profile lists its overrides in its
   digest, as bullets naming the label and the reason. A project may change the default
   again in its own `## Overrides`. Floors stay fixed. A profile may also define a
   labelled default of its own, and `project check` accepts labels from the kernel and
   from the profile blocks the project pins.
4. **Authority over written organization and client rules.** The owner's in-session
   instruction overrides a written organization or client rule only once the owner
   confirms that the organization or client agreed to the change. The agent then updates
   the written rule in the same change, naming who agreed and when, so the next session
   does not follow the old rule.
5. **Spending.** `default:no-billed` leaves the kernel. The kernel keeps one unlabelled
   rule, outside the floors: use no paid service, plan, or metered usage that the
   project or its profile has not approved. Preferences move to profiles:
   - `personal`: free first, weighed against quality. When a paid option is clearly
     better, the agent proposes it with the free alternative, a monthly cost estimate,
     and the quality gained. The owner approves, and the project records the service and
     its budget.
   - `freelance`: no free-first preference. Whose account pays and what is approved are
     settled per engagement and recorded in the project.
   - The work profile: model calls through the organization's gateway are paid and
     metered by the organization, and a project records a budget only when one is set
     for it. Other paid services need the organization's approval, which the owner
     confirms to the agent.
   This supersedes ADR 0010 decision 1. ADR 0010's runner policy (hosted runners on
   public repositories, the self-hosted runner on private ones) becomes `personal`
   profile content.
6. **Merging.** Agents never merge or approve (floor). The owner reviews and merges
   unless the profile or project names someone else, by role.
7. **Kernel lines that move out:**
   - "one per device for the owner's devices, one per project for shared builders" goes
     to `release-delivery`. The floor keeps the dedicated, least-privilege credential.
   - "A proposal (`pakem propose`) opens first ..." goes to the `pakem` and
     `instruction-maintenance` skills, which already describe it.
   - The `pakem` skill row routes server setup and releases to `pakem` only where the
     profile or project assigns them. Device setup, project checks and decisions, and
     framework proposals stay universal.
   - Formatter, linter, and CI from the first commit apply to repositories the owner
     creates. Existing repositories keep their own setup.
8. **Kernel lines confirmed universal** (owner, 2026-10-27): OpenSpec for meaningful
   changes (the owner's default workflow until a better method is shown),
   `default:commits`, `default:branching`, `default:mr-policy`, `default:python-uv`,
   `default:state-untracked`, and `research/`.

Considered and rejected:

- A free-first rule in the kernel that work projects override. Every organization and
  client project would carry the same override, and the invented-budget failure would
  stay possible wherever one is missing.
- Profile by workspace folder (a profile file at each workspace root). Codex and
  Antigravity stop reading instruction files at the repository root, and cloud agents
  never see the folder, so the rules would not reach them.
- A separate generic work profile beside the organization profile. The owner stays with
  one employer for long periods, so the extra layer would add sync work for little gain.
- The spending rule as a floor. A floor change is a MAJOR release, and the rule needs no
  more protection than an unlabelled rule already has: no file may override it.

## Consequences

- Kernel text: section 1 gains the authority rule, the profile pin, and profile
  overrides. Section 2 shortens the credential floor and names who merges. Section 4
  replaces `default:no-billed` with the spending rule and scopes the formatter rule.
  Section 6 drops the proposal sentence. Section 8 scopes the `pakem` row. The kernel stays
  within budget (150 lines, about 9,200 characters). The line "the formatter is
  authoritative for layout" folds into "follow the project's ... formatter".
- Compatibility: three existing projects carry a `default:no-billed` override. Once the
  kernel no longer defines that label, `project check` would report each as an unknown
  label (an error), and existing projects would break. So `default:no-billed` becomes a
  retired label: `project check` warns and names its replacement (a `personal` profile
  label, or deletion), never an error. With that, the release is MINOR.
- Mechanism, in the same change as the kernel text: `project check` reads labels and
  overrides from the pinned profile blocks, warns on a retired label, and warns when a
  project pins no profile (it cannot tell a context profile from an organization one).
  Until the `personal` profile exists, the owner's personal projects see that warning. The billing warning and the budget requirement in
  the `gateway.profile` gap are removed. The gap asks only which gateway the project uses.
- Profile work, after this ADR: the `personal` and `freelance` digests, synced into the
  owner's personal and client repositories, and the work profile's new lines in its own
  repository. The profile digest budget (1,600 characters) is open: the work profile's
  digest is already near it.
- Text to update with it: README ("Free by default"), ADR 0011 decision 5,
  `docs/pakem-command-map.md`, `release-delivery` (SKILL.md, the runner policy in
  `references/ci.md`, the credential split), `instruction-maintenance` (the admission
  test and profile overrides), and `genai-engineering`
  (`references/cost-latency.md`: choose the model by the project's quality evals, then a
  balance of performance and acceptable cost, replacing "the smallest model that passes
  the evals").
- Out of scope, each its own proposal: a `pakem` documents area for freelance engagement
  records, and `pakem release` running each project's declared release with the
  `artifact` kind.
- Other repositories, from their own sessions: the work project's invented budget is
  removed (the work profile line replaces it), and the owner's personal projects sync
  the `personal` profile.
