#!/usr/bin/env bash
# PreToolUse(Bash) allowlist for the ghmem agentic layer. It holds both
# ghmem-triage and ghmem-synthesize to one sanctioned data path, a bare
# `ghmem` invocation over the local corpus — see spec
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist hook.
#
# The plugin's hooks/hooks.json registers it for every session that enables
# the plugin, because Claude Code ignores `hooks:` in a plugin agent's own
# frontmatter. So it first asks whose call this is, and lets through
# untouched any that is not one of those two agents'.
#
# Positive character allowlist, not a metacharacter denylist: none of
# `; & | $ \` < > ( )` or newline is in the permitted class, so chaining,
# substitution, pipes, and redirects are excluded by construction rather than
# enumerated.
#
# That class cannot carry a note, and a note is the reason `route` exists.
# So one shape is allowed past it whole: a heredoc with a quoted delimiter.
# Quoted means bash expands nothing in the body, which is what makes an
# arbitrary body safe to permit — the body is data, and the only thing that
# could turn it back into instructions is the delimiter line appearing early,
# so that is what the checks below forbid.
#
# The opener names its own delimiter rather than being held to a fixed word.
# A fixed one is a word a note may never contain, and a note is prose about a
# corpus whose vocabulary nobody controls; the writer picking a word their own
# note does not contain is the same guarantee without the exclusion.
#
# The delimiter is held to letters, digits and underscores. Single quotes
# would in fact make any byte but the quote itself inert — bash expands
# nothing in a heredoc delimiter word and quote removal returns it verbatim,
# so what this hook captures is what bash will match. But that safety is then
# a proof about quoting rather than something visible in the pattern, and it
# would rest entirely on the quote's exclusion. Nobody needs a delimiter with
# a `$` in it, so the narrow class costs nothing and is checkable at sight.
#
# An empty body is refused as well. A heredoc carrying nothing is malformed
# whichever verdict it accompanies: it opened a note and then supplied none.
# That is a fact about the command's shape, so it belongs here; which
# verdicts want a note is a domain rule and stays in `record_verdict`.
set -Eeuo pipefail

# Any exit but 2 reads to Claude Code as "not a block", so a failure in the
# hook itself — jq missing, a payload that does not parse — would let the
# call through. A containment hook that cannot decide refuses instead. A
# command substitution inherits the trap, so only the outermost shell speaks.
trap '((BASH_SUBSHELL)) || echo "ghmem-only: blocked — the hook itself failed (is jq installed?)" >&2; exit 2' ERR

payload="$(cat)"

# Assigned before it is tested: a command substitution that fails inside a
# `case` word is not an error to bash, and would fall through to the pass.
agent_type="$(jq -r '.agent_type // empty' <<<"$payload")"
case "$agent_type" in
  ghmem:ghmem-triage | ghmem:ghmem-synthesize) ;;
  *) exit 0 ;;
esac

tab=$'\t'
sep="[ ${tab}]"
arg="[A-Za-z0-9_./:=-]+"
bare_re="^ghmem(${sep}${arg})*$"
opener_re="^ghmem(${sep}${arg})*${sep}<<'([A-Za-z0-9_]+)'$"

block() {
  echo "ghmem-only: blocked — allowed are a bare 'ghmem ...' invocation and" \
    "one ending \"<<'DELIM'\" for a DELIM of your choosing (letters, digits" \
    "and underscores), carrying a non-empty note body closed by a final" \
    "DELIM line and naming it nowhere else" \
    "(no chaining, substitution, pipes, or redirects)" >&2
  exit 2
}

command="$(jq -r '.tool_input.command' <<<"$payload")"

if [[ "$command" != *$'\n'* ]]; then
  [[ "$command" =~ $bare_re ]] || block
  exit 0
fi

[[ "${command%%$'\n'*}" =~ $opener_re ]] || block

# Group 1 is the repeated argument group, so the delimiter is group 2. Every
# rule below is anchored to it: what the opener named is what has to close the
# body, and nothing weaker counts.
delim="${BASH_REMATCH[2]}"

# One optional trailing newline is the shell's, not the body's.
body="${command#*$'\n'}"
body="${body%$'\n'}"
mapfile -t lines <<<"$body"

last=$((${#lines[@]} - 1))
[[ "${lines[last]}" == "$delim" ]] || block

note=""
for ((i = 0; i < last; i++)); do
  [[ "${lines[i]}" == "$delim" ]] && block
  note+="${lines[i]}"
done
[[ "$note" =~ [^[:space:]] ]] || block

exit 0
