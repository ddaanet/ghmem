#!/usr/bin/env bash
# PreToolUse(Write|Edit) hook, carried by ghmem-synthesize only. Scopes its
# Write/Edit tool access to docs/design/ — the one directory that agent is
# meant to write. See spec
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist
# hook (write-path scope).
#
# There is no frontmatter field for directory scoping, and permission rules
# only auto-approve rather than hard-deny (a Write(...) rule is silently
# unenforced), so this hook is the only hard guarantee.
set -euo pipefail

payload="$(cat)"
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

echo "scope-docs-design: blocked — Write/Edit is scoped to docs/design/ only ($resolved is outside $design_dir)" >&2
exit 2
