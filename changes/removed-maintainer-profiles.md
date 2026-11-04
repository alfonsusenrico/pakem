- The maintainer's `personal` and `freelance` profiles left this repository for the
  maintainer's own private one, with their history (ADR 0001 amended). Only
  `profiles/example` remains, as the starting point for anyone's profile
  (`docs/profiles.md`). An install that passed `--profile <framework>/profiles/personal`
  or `.../freelance` must point at the profiles' new location with one
  `pakem install --profile <dir> ... --apply` before its next `pakem update`.
