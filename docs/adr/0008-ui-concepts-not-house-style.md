# 0008. UI guidance is about purpose, not house style

- Status: Accepted
- Date: 2026-10-13

## Context

The `ui-ux-quality` skill carried an owner design base (radius, one accent, type
weights, uppercase rules, icon style, AI-surface styling) and three token sets
(`owner-studio` approved; `owner-dashboard` and `owner-workbench` drafts), about 35 KB.
Each product already keeps its own tokens, so copies in the framework had to be kept in
sync and pushed a single look onto unrelated products. What agents actually get wrong
across projects is the purpose of an interface: a dashboard for reading much data, a
public website for strangers, an application with many features.

## Decision

1. Visual style - colour, type, shape, icons, motion, tone - is a project decision,
   recorded in each project's tokens file and design notes.
2. The skill starts from what the interface is for (`references/surfaces.md`): a short
   method plus worked examples for a data dashboard, a public website, and a web
   application, each with goal, questions, principles from cited research, and checks.
   The agent decides which a product mostly is; the examples are not a closed list.
3. Kept for every interface: truthful content, WCAG 2.2 AA with evidence, light and
   dark themes, meaning never by colour alone, phone layouts designed for the phone,
   and choosing a direction from working alternatives.

## Consequences

- Products may look different from each other; consistency comes from each project's
  own tokens and design notes.
- The token files and the house-style reference are removed; projects that mention
  them keep their own implemented tokens as the source.
