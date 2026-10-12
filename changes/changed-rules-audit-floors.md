- Kernel floors: ask before deleting data or files the current task did not create;
  agents never merge or approve a merge request (ADR 0006).
- `default:mr-policy`: agents push the topic branch, open the merge request, and report
  its URL. `default:branching`: `main` stays deployable, documentation-only changes may
  go directly to `main`, and a project may allow more.
