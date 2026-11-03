- `pakem host runner add <host> <owner/repo> --count N` (1 to 8) registers several runners
  for one repository, so its independent jobs run side by side instead of queueing on a
  single runner. `remove` removes all of them. The runner slice's default caps follow the
  host: half its CPUs and 40% of its memory, at least 2 CPUs and 4 GiB (was a fixed 2 CPUs
  and 4 GiB). An existing host takes the new caps when `pakem setup host` runs again.
