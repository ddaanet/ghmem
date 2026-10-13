#!/usr/bin/env bash
# SessionStart hook: names any missing dependency of the containment hooks.
#
# ghmem-only.sh, scope-packets-only.sh and scope-docs-design.sh run on every
# Bash, Read, Write and Edit call of every session that enables the plugin,
# so when one cannot run it lets the call through, silently, rather than
# bricking those tools. This report is where a missing dependency shows
# instead: once per session, to the user on `systemMessage` and to the agent
# on `additionalContext`, since it is the agent that dispatches ghmem's.
#
# It cannot use jq to build its JSON, jq being one of the things it reports.
# Every string it prints is fixed text with nothing to escape. Present
# dependencies print nothing.
set -uo pipefail

missing=()
command -v jq >/dev/null 2>&1 || missing+=("jq")
realpath -m -- / >/dev/null 2>&1 || missing+=("GNU realpath (coreutils)")

((${#missing[@]})) || exit 0

names="${missing[0]}"
((${#missing[@]} > 1)) && names+=" and ${missing[1]}"

user="ghmem: missing ${names} — its containment hooks pass every call through until installed."
agent="ghmem's containment hooks cannot run in this session (missing: ${names}), so they pass every call through. Do not dispatch ghmem:ghmem-triage or ghmem:ghmem-synthesize: both read untrusted GitHub text and would run uncontained."

printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' \
  "$user" "$agent"
