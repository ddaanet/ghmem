#!/usr/bin/env bash
# PreToolUse(Read) hook, acting for ghmem-triage only. Scopes its Read access
# to corpus/packets/ — the one directory `packet-file:` lines can
# ever point at (see `paths.packet_dir`).
#
# Without this, the only thing standing between a corpus item's rendered
# GitHub text and an arbitrary-file Read is the agent's own judgment that a
# `packet-file:` line it sees is the genuine one `serve_next` spliced in,
# not a line some issue or comment body happens to contain — corpus content
# is attacker-reachable (anyone can file a GitHub issue), so that judgment
# is not a security boundary. This hook is: whatever path the agent was
# steered toward, Read still refuses anything outside the packet directory.
#
# The plugin's hooks/hooks.json registers it for every session that enables
# the plugin, because Claude Code ignores `hooks:` in a plugin agent's own
# frontmatter. So it first asks whose call this is, and lets through
# untouched any that is not that agent's — ghmem-synthesize's included,
# whose Read is scope-docs-design.sh's to hold.
set -euo pipefail

# A hook that cannot decide — jq missing, a realpath without GNU's `-m`, a
# payload that does not parse — lets the call through, silently. It runs on
# every Read call of every session that enables the plugin, so refusing
# would brick those tools on a machine missing either, and a non-2 exit with
# stderr shows as a hook error on every call. check-deps.sh reports the
# missing dependency once, at SessionStart.

payload="$(cat)"

# Assigned before it is tested: a command substitution that fails inside a
# `case` word is not an error to bash, and would fall through unnoticed.
agent_type="$(jq -r '.agent_type // empty' <<<"$payload" 2>/dev/null)" || exit 0
case "$agent_type" in
  ghmem:ghmem-triage) ;;
  *) exit 0 ;;
esac

file_path="$(jq -r '.tool_input.file_path' <<<"$payload" 2>/dev/null)" || exit 0
cwd="$(jq -r '.cwd // empty' <<<"$payload" 2>/dev/null)" || exit 0
project_dir="${CLAUDE_PROJECT_DIR:-$cwd}"

if [[ "$file_path" != /* ]]; then
  file_path="${cwd:-$project_dir}/$file_path"
fi

resolved="$(realpath -m -- "$file_path" 2>/dev/null)" || exit 0
packet_dir="$(realpath -m -- "$project_dir/corpus/packets" 2>/dev/null)" || exit 0

case "$resolved" in
  "$packet_dir"/*) exit 0 ;;
esac

echo "scope-packets-only: blocked — Read is scoped to corpus/packets/ only ($resolved is outside $packet_dir)" >&2
exit 2
