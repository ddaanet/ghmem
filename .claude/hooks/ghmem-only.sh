#!/usr/bin/env bash
# PreToolUse(Bash) allowlist for the ghmem agentic layer. Both ghmem-triage
# and ghmem-synthesize carry this hook so their only sanctioned data path is
# a bare `ghmem` invocation over the local corpus — see spec
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist hook.
#
# Positive character allowlist, not a metacharacter denylist: none of
# `; & | $ \` < > ( )` or newline is in the permitted class, so chaining,
# substitution, pipes, and redirects are excluded by construction rather than
# enumerated.
set -euo pipefail

command="$(jq -r '.tool_input.command' <<<"$(cat)")"

if [[ "$command" =~ ^ghmem([\ $'\t'][A-Za-z0-9_./:=-]+)*$ ]]; then
  exit 0
fi

echo "ghmem-only: blocked — only a bare 'ghmem ...' invocation is allowed (no chaining, substitution, pipes, or redirects)" >&2
exit 2
