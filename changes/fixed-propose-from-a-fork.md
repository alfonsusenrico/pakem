- `pakem propose` works for a login that may not push to the framework repository, such as
  someone who installed Pakem from upstream. It forks the repository to that login once
  (the `pakem-fork` remote of the framework checkout), pushes the proposal's branch there,
  and opens the pull request from the fork. `revise` pushes to the same place. A login that
  may push, including anyone whose checkout follows their own fork, works as before.
