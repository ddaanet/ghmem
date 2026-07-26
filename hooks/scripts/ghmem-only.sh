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
#
# That class cannot carry a note, and a note is the reason `route` exists.
# So one shape is allowed past it whole: a heredoc with a fixed, quoted
# delimiter. Quoted means bash expands nothing in the body, which is what
# makes an arbitrary body safe to permit — the body is data, and the only
# thing that could turn it back into instructions is the delimiter line
# appearing early, so that is what the checks below forbid.
#
# An empty body is refused as well. A heredoc carrying nothing is malformed
# whichever verdict it accompanies: it opened a note and then supplied none.
# That is a fact about the command's shape, so it belongs here; which
# verdicts want a note is a domain rule and stays in `record_verdict`.
set -euo pipefail

readonly DELIM='GHMEMNOTE'

tab=$'\t'
sep="[ ${tab}]"
arg="[A-Za-z0-9_./:=-]+"
bare_re="^ghmem(${sep}${arg})*$"
opener_re="^ghmem(${sep}${arg})*${sep}<<'${DELIM}'$"

block() {
  echo "ghmem-only: blocked — allowed are a bare 'ghmem ...' invocation and" \
    "one ending '--note <<'${DELIM}'' with a non-empty note body closed by a" \
    "final ${DELIM} line (no chaining, substitution, pipes, or redirects)" >&2
  exit 2
}

command="$(jq -r '.tool_input.command' <<<"$(cat)")"

if [[ "$command" != *$'\n'* ]]; then
  [[ "$command" =~ $bare_re ]] || block
  exit 0
fi

[[ "${command%%$'\n'*}" =~ $opener_re ]] || block

# One optional trailing newline is the shell's, not the body's.
body="${command#*$'\n'}"
body="${body%$'\n'}"
mapfile -t lines <<<"$body"

last=$((${#lines[@]} - 1))
[[ "${lines[last]}" == "$DELIM" ]] || block

note=""
for ((i = 0; i < last; i++)); do
  [[ "${lines[i]}" == "$DELIM" ]] && block
  note+="${lines[i]}"
done
[[ "$note" =~ [^[:space:]] ]] || block

exit 0
