#!/usr/bin/env bash
# PreToolUse hook: a wrapper for `ghmem-hook scope-packets-only`, which holds the logic
# and says what it guards. hooks.json registers the script rather than the
# console script because the command has to be the quoted
# "${CLAUDE_PLUGIN_ROOT}/..." form, which resolves wherever the plugin is
# installed.
#
# Where `ghmem-hook` is not on PATH the call passes through, silently: the
# hook runs on every matching call of every session that enables the plugin,
# so refusing would brick those tools, and a non-2 exit with stderr shows as
# a hook error on every call. check-deps.sh reports it once, at SessionStart,
# and each agent's opening containment check catches the call that got by.
command -v ghmem-hook >/dev/null 2>&1 || exit 0
exec ghmem-hook scope-packets-only
