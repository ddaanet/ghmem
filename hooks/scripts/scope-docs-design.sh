#!/usr/bin/env bash
# PreToolUse(Read|Write|Edit) hook, acting for ghmem-synthesize only. Scopes
# its file tools to docs/design/ — the one directory that agent is meant to
# write. See spec
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist
# hook (write-path scope).
#
# Read is held to the same directory as Write and Edit. The agent needs it
# only to open the doc it is about to rewrite, and it works from untrusted
# corpus text into docs that get committed: with Read unscoped, an injected
# instruction could have it copy any file on the machine into one.
#
# There is no frontmatter field for directory scoping, and permission rules
# only auto-approve rather than hard-deny (a Write(...) rule is silently
# unenforced), so this hook is the only hard guarantee.
#
# The plugin's hooks/hooks.json registers it for every session that enables
# the plugin, because Claude Code ignores `hooks:` in a plugin agent's own
# frontmatter. So it first asks whose call this is, and lets through
# untouched any that is not that agent's.
set -Eeuo pipefail

# Any exit but 2 reads to Claude Code as "not a block", so a failure in the
# hook itself — jq missing, a realpath without GNU's `-m` — would let the
# call through. A containment hook that cannot decide refuses instead. A
# command substitution inherits the trap, so only the outermost shell speaks.
trap '((BASH_SUBSHELL)) || echo "scope-docs-design: blocked — the hook itself failed (are jq and GNU realpath installed?)" >&2; exit 2' ERR

payload="$(cat)"

# Assigned before it is tested: a command substitution that fails inside a
# `case` word is not an error to bash, and would fall through to the pass.
agent_type="$(jq -r '.agent_type // empty' <<<"$payload")"
case "$agent_type" in
  ghmem:ghmem-synthesize) ;;
  *) exit 0 ;;
esac

file_path="$(jq -r '.tool_input.file_path' <<<"$payload")"
cwd="$(jq -r '.cwd // empty' <<<"$payload")"
project_dir="${CLAUDE_PROJECT_DIR:-$cwd}"

if [[ "$file_path" != /* ]]; then
  file_path="${cwd:-$project_dir}/$file_path"
fi

resolved="$(realpath -m -- "$file_path")"
design_dir="$(realpath -m -- "$project_dir/docs/design")"

case "$resolved" in
  "$design_dir"|"$design_dir"/*) exit 0 ;;
esac

echo "scope-docs-design: blocked — Read, Write and Edit are scoped to docs/design/ only ($resolved is outside $design_dir)" >&2
exit 2
