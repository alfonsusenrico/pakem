- The kernel holds only rules that apply in personal, work, and client repositories
  (ADR 0012). `default:no-billed` leaves it: one rule remains, use no paid service, plan,
  or metered usage the project or its profile has not approved, and free-first moves to
  the `personal` profile. Profiles may change labelled defaults, each project pins one
  context profile, an owner instruction overrides a written organization or client rule
  only once that party agreed, and the person who merges is named by the profile or
  project.
- `pakem project check` accepts override labels that pinned profiles define, warns on the
  retired `default:no-billed` label instead of failing, and warns when a project pins no
  profile. It no longer asks `llm` projects for a budget, and the `gateway.profile` gap
  asks only which gateway the project uses.
