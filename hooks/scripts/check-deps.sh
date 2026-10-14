#!/usr/bin/env bash
# SessionStart hook: names the one dependency of the containment hooks.
#
# ghmem-only.sh, scope-packets-only.sh and scope-docs-design.sh are wrappers
# for `ghmem-hook`, and run on every Bash, Read, Write and Edit call of every
# session that enables the plugin. When it is not on PATH they let the call
# through, silently, rather than bricking those tools. This report is where
# that shows instead: once per session, to the user on `systemMessage` and to
# the agent on `additionalContext`, since it is the agent that dispatches
# ghmem's.
#
# It builds its JSON with printf because it cannot rely on any other tool
# being there. Every string it prints is fixed text with nothing to escape. A
# present `ghmem-hook` prints nothing.
set -uo pipefail

command -v ghmem-hook >/dev/null 2>&1 && exit 0

user="ghmem: ghmem-hook is not on PATH — its containment hooks pass every call through until ghmem is installed."
agent="ghmem's containment hooks cannot run in this session (ghmem-hook is not on PATH), so they pass every call through. Do not dispatch ghmem:ghmem-triage or ghmem:ghmem-synthesize: both read untrusted GitHub text and would run uncontained."

printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' \
  "$user" "$agent"
