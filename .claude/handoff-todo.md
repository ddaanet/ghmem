## Open decisions

- `plugin-dev/bin/claude` carries a marked LOCAL HACK block (appends `dist/plugin/bin` to PATH) in the vendored toolkit subtree, which `just update-plugin-dev` overwrites and which the release flow's clean-tree check will see. Decide whether it ships in the release commit or is reverted first; the real fix is the brief `brief-dogfood-shim-plugin-bin-path.md` in `/Users/david/code/claude-plugin-dev/inbox`.
- Whether to squash `ae4105b` into `31009a8`: `ae4105b` omitted `src/ghmem/hook.py`, so it does not run on its own. Nothing is pushed.

## Remaining

- Cut the patch release with the toolkit flow in `plugin-dev/README.md` (`plugin-craft:toolkit-release` covers what goes wrong); `just precommit` must be green first.
- Probe a marketplace-installed copy (no `CLAUDE_CODE_PLUGIN_DIRS`): hooks contained, `bin/ghmem` on a plugin agent's PATH, the SessionStart build running; `tmp/ghmem-negative-probe.sh` covers only the shim cases.
- Fix `TODO.md`: `ghmem status` in a git repo with no `corpus/` creates an empty `corpus/cache.sqlite` and dies with a raw sqlite traceback.
- Re-run `/code-review aa52e94..HEAD` on the final tree.
- Read `inbox/todo-carried-from-micro.md`, not yet read.
- Propose, do not edit, micro's removal of its three source facts (`ghmem-project`, `ghmem-ongoing-sync-not-snapshot`, `sqlalchemy-uow-hides-mutations`) once the micro-craft side lands; the micro session owns the `plugin-hook-fail-open.md` revision.
- Run `/gitlore:index-audit`: the root memory index is over the loader budget.