## Open decisions

- Whether to squash ae4105b into 31009a8. ae4105b omitted `src/ghmem/hook.py` (a `git add -u` that skipped an untracked file), so it does not run on its own; 31009a8 added the file. Nothing is pushed, so a rewrite is possible, and the cost of leaving it is one non-bisectable commit.

## Remaining

- Re-run `/code-review aa52e94..HEAD` on the final tree; brief 2's acceptance check (the review finds nothing new) was not closed after the last fixes.
- Read `inbox/todo-carried-from-micro.md`, which has not been read this session.
- Remove micro's three source facts (`ghmem-project`, `ghmem-ongoing-sync-not-snapshot`, `sqlalchemy-uow-hides-mutations`) only after the micro-craft side lands (needs 5.1 there); micro is another repo, so propose, do not edit.
- The micro session revises `memory/ddaanet/plugin-hook-fail-open.md` from `/Users/david/code/micro/inbox/brief-ghmem-hook-fail-open-fact.md`; do not edit micro.
- Run `/gitlore:index-audit` when wanted: root memory/MEMORY.md is over the loader budget.