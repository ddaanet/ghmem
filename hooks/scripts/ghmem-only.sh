#!/usr/bin/env bash
# PreToolUse hook on Bash: a wrapper for `ghmem-only` in src/ghmem/hook.py,
# which holds the logic and says what it guards. hooks.json registers the
# script rather than the module because the command has to be the quoted
# "${CLAUDE_PLUGIN_ROOT}/..." form, which resolves wherever the plugin is
# installed. The module is found from this script's own location, so the hook
# runs from the plugin's tree and needs nothing installed but a python3; `-I`
# keeps PYTHONPATH and the module's directory off sys.path.
#
# Where there is no python3, or the module is missing from the tree (python3
# exits 2 on a script it cannot open, which Claude Code reads as a block), the
# call passes through, silently: the hook runs on every matching call of every
# session that enables the plugin, so refusing would brick those tools, and a
# non-2 exit with stderr shows as a hook error on every call. check-deps.sh
# reports it at SessionStart, and each agent's opening containment check
# catches the call that got by.
command -v python3 >/dev/null 2>&1 || exit 0
root="$(CDPATH='' cd -P -- "${0%/*}/../.." && pwd)"
[[ -f "$root/src/ghmem/hook.py" ]] || exit 0
exec python3 -I "$root/src/ghmem/hook.py" ghmem-only
