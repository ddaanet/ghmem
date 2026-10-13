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
set -Eeuo pipefail

# Any exit but 2 reads to Claude Code as "not a block", so a failure in the
# hook itself — jq missing, a realpath without GNU's `-m` — would let the
# call through. A containment hook that cannot decide refuses instead. A
# command substitution inherits the trap, so only the outermost shell speaks.
trap '((BASH_SUBSHELL)) || echo "scope-packets-only: blocked — the hook itself failed (are jq and GNU realpath installed?)" >&2; exit 2' ERR

payload="$(cat)"

# Assigned before it is tested: a command substitution that fails inside a
# `case` word is not an error to bash, and would fall through to the pass.
agent_type="$(jq -r '.agent_type // empty' <<<"$payload")"
case "$agent_type" in
  ghmem:ghmem-triage) ;;
  *) exit 0 ;;
esac

file_path="$(jq -r '.tool_input.file_path' <<<"$payload")"
cwd="$(jq -r '.cwd // empty' <<<"$payload")"
project_dir="${CLAUDE_PROJECT_DIR:-$cwd}"

if [[ "$file_path" != /* ]]; then
  file_path="${cwd:-$project_dir}/$file_path"
fi

resolved="$(realpath -m -- "$file_path")"
packet_dir="$(realpath -m -- "$project_dir/corpus/packets")"

case "$resolved" in
  "$packet_dir"/*) exit 0 ;;
esac

echo "scope-packets-only: blocked — Read is scoped to corpus/packets/ only ($resolved is outside $packet_dir)" >&2
exit 2
