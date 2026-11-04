- A project that overrides `default:commits` in its `AGENTS.md` `## Overrides` keeps its own
  commit format. The guard and the commit-msg and pre-push hooks used to refuse every body
  and trailer anyway, so a project that needs sign-off or ticket trailers could not follow
  its own rule. They now leave such a project's messages to it, and `--no-verify` stays
  blocked. Codex's static rules protect the common branch names `main`, `master`,
  `develop`, and `development` (adding git-flow's `develop`). `pakem update` lists the
  projects to align with the tool it just installed, so the list follows the new rules, and
  `pakem project follow-ups` prints it.
- Projects: a project that overrides `default:commits` runs `pakem hooks install <repo>
  --apply` again to get hooks that honour it.
