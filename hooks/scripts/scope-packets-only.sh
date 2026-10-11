#!/usr/bin/env bash
# PreToolUse(Read) hook, carried by ghmem-triage only. Scopes its Read access
# to tools/ghmem/tmp/packets/ — the one directory `packet-file:` lines can
# ever point at (see `paths.packet_dir`).
#
# Without this, the only thing standing between a corpus item's rendered
# GitHub text and an arbitrary-file Read is the agent's own judgment that a
# `packet-file:` line it sees is the genuine one `serve_next` spliced in,
# not a line some issue or comment body happens to contain — corpus content
# is attacker-reachable (anyone can file a GitHub issue), so that judgment
# is not a security boundary. This hook is: whatever path the agent was
# steered toward, Read still refuses anything outside the packet directory.
set -euo pipefail

payload="$(cat)"
file_path="$(jq -r '.tool_input.file_path' <<<"$payload")"
cwd="$(jq -r '.cwd // empty' <<<"$payload")"
project_dir="${CLAUDE_PROJECT_DIR:-$cwd}"

if [[ "$file_path" != /* ]]; then
  file_path="${cwd:-$project_dir}/$file_path"
fi

resolved="$(realpath -m -- "$file_path")"
packet_dir="$(realpath -m -- "$project_dir/tools/ghmem/tmp/packets")"

case "$resolved" in
  "$packet_dir"/*) exit 0 ;;
esac

echo "scope-packets-only: blocked — Read is scoped to tools/ghmem/tmp/packets/ only ($resolved is outside $packet_dir)" >&2
exit 2
