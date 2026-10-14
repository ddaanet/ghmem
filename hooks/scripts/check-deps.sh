#!/usr/bin/env bash
# SessionStart hook: names the one dependency of the containment hooks.
#
# ghmem-only.sh, scope-packets-only.sh and scope-docs-design.sh are wrappers
# for src/ghmem/hook.py, run by `python3 -I` from the plugin's own tree, on
# every Bash, Read, Write and Edit call of every session that enables the
# plugin. When python3 is missing or the module will not run they let the call
# through, silently, rather than bricking those tools. This report is where
# that shows instead: at session start and again after resume, clear and
# compact (the agent's copy of the warning is gone after the last two), to the
# user on `systemMessage` and to the agent on `additionalContext`, since it is
# the agent that dispatches ghmem's.
#
# It builds its JSON with printf because it cannot rely on any other tool
# being there. Every string it prints is fixed text with nothing to escape. A
# hook module that runs prints nothing.
set -uo pipefail

root="$(CDPATH='' cd -P -- "${0%/*}/../.." && pwd)"

# Run it, not just find it: a python3 too old for the module, or a module that
# fails to import, exits non-0 and non-2 on every call, which Claude Code reads
# as "not a block". A payload naming no agent is the cheapest call that must
# exit 0.
command -v python3 >/dev/null 2>&1 &&
  python3 -I "$root/src/ghmem/hook.py" ghmem-only <<<'{}' >/dev/null 2>&1 && exit 0

user="ghmem: python3 is missing or cannot run the plugin's hook module (src/ghmem/hook.py) — its containment hooks pass every call through until it can."
agent="ghmem's containment hooks cannot run in this session (python3 is missing or cannot run the plugin's hook module), so they pass every call through. Do not dispatch ghmem:ghmem-triage or ghmem:ghmem-synthesize: both read untrusted GitHub text and would run uncontained."

printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' \
  "$user" "$agent"
