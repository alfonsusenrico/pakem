- The framework repository has CI: lint and the unit tests on Python 3.11 and 3.14 for
  every pull request and `main`, on GitHub-hosted runners (actions pinned by SHA). A test
  used f-string syntax that needs Python 3.12; it now runs on 3.11, the oldest supported.
