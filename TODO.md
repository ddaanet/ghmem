# TODO

- `ghmem status` (and `triage`) in a git repository with no `corpus/` creates
  an empty `corpus/cache.sqlite` and exits with a raw
  `sqlite3.OperationalError: no such table` traceback. It should say there is
  no corpus here (and name `corpus/target.toml` / `ghmem sync`), and create
  nothing. Reproduced in a fresh `git init` directory; found when the dogfood
  probe ran from this repo, which has no corpus.
