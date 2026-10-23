- `pakem project init` resumes an init that stopped part-way: before its commit it writes
  only the missing files and commits; after it, it creates the GitHub repository (unless
  origin exists) and pushes. Before, init refused the non-empty directory and adopt
  needed a repository that did not exist yet.
