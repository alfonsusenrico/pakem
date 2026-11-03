- Projects that never recorded their alignment with Pakem show their follow-ups again. The
  fix released in 2.3.0 was undone on `main` by the next merge, whose change was drafted
  before it, so `pakem project check` again showed such projects none of the notes the
  2.3.0 release had consumed. `pakem propose` now refuses a drafted change that deletes a
  change note: only a release deletes them, and such a change was drafted from an older
  `main` and would undo what merged since.
