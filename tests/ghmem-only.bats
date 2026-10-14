#!/usr/bin/env bats
# hooks/scripts/ghmem-only.sh — the PreToolUse(Bash) allowlist hook that keeps
# ghmem-triage/ghmem-synthesize on their only sanctioned data path. Spec:
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist hook.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/ghmem-only.sh"

setup() {
  # The wrappers exec the first ghmem-hook on PATH; this tree's must be it.
  PATH="$BATS_TEST_DIRNAME/../.venv/bin:$PATH"
}

# The hook reads .agent_type, then .tool_input.command; the rest of the
# PreToolUse envelope is irrelevant to it, so tests omit it. AGENT_TYPE picks
# the agent, and setting it empty drops the key the way a main-thread payload
# does.
payload() {
  jq -n --arg cmd "$1" --arg agent "${AGENT_TYPE-ghmem:ghmem-triage}" \
    '{tool_name: "Bash", tool_input: {command: $cmd}}
     + if $agent == "" then {} else {agent_type: $agent} end'
}

run_hook() {
  payload "$1" | bash "$HOOK"
}

# The hook is registered for every session that enables the plugin, so what
# it does to a call that is not a ghmem agent's is as load-bearing as what it
# does to one that is.
@test "ghmem-only: passes any command through, silently, when no agent is named" {
  AGENT_TYPE=""
  run --separate-stderr run_hook "rm -rf /"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

# The match is on the whole name. The bare form is a project's own
# `.claude/agents/` definition, which is not this plugin's to police.
@test "ghmem-only: passes any command through for an agent that is not the plugin's" {
  for AGENT_TYPE in general-purpose other:ghmem-triage ghmem-triage \
    ghmem:ghmem-triage-2 xghmem:ghmem-triage; do
    run run_hook "rm -rf /"
    [ "$status" -eq 0 ]
  done
}

@test "ghmem-only: holds ghmem-synthesize to the same allowlist" {
  AGENT_TYPE=ghmem:ghmem-synthesize
  run run_hook "ghmem status"
  [ "$status" -eq 0 ]
  run run_hook "rm -rf /"
  [ "$status" -eq 2 ]
}

# A hook that cannot decide lets the call through, silently: refusing would
# stop Bash in every session that enables the plugin, and a non-2 exit with
# stderr shows as a hook error on every call. The missing dependency is
# reported by check-deps.sh at SessionStart.
@test "ghmem-only: passes through, silently, when the payload is not JSON" {
  run --separate-stderr bash "$HOOK" <<<"not json"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "ghmem-only: passes through, silently, when ghmem-hook is not on PATH" {
  mkdir "$BATS_TEST_TMPDIR/empty"
  body="$(payload "rm -rf /")"
  run --separate-stderr env -i PATH="$BATS_TEST_TMPDIR/empty" "$BASH" "$HOOK" <<<"$body"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "ghmem-only: allows a bare ghmem invocation with no arguments" {
  run run_hook "ghmem"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows ghmem with simple subcommand args" {
  run run_hook "ghmem triage"
  [ "$status" -eq 0 ]
}

# The hook polices a command's shape, not a subcommand's arity: a `route`
# missing its heredoc is well-formed here and rejected by the CLI, which is
# the layer that knows a verdict needs a note.
@test "ghmem-only: allows a single-line ghmem route" {
  run run_hook "ghmem route abc123 42 softwrapcolumn"
  [ "$status" -eq 0 ]
}

# The pairing matters: a guard broken closed passes every deny case, so the
# real `unroute` string is asserted ALLOW beside a must-block sibling.
@test "ghmem-only: allows a multi-number ghmem unroute" {
  run run_hook "ghmem unroute 1445 3795 3783"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: blocks a ghmem unroute with a chained command" {
  run run_hook "ghmem unroute 1445; rm -rf /"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: allows args containing dots, slashes, colons, equals, underscores" {
  run run_hook "ghmem show docs/design/foo.md --format=json item_42:1"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: blocks a non-ghmem command entirely" {
  run run_hook "rm -rf /"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a chained ghmem ...; rm ..." {
  run run_hook "ghmem status; rm -rf /"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks ghmem && chained command" {
  run run_hook "ghmem status && rm -rf /"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a piped command" {
  run run_hook "ghmem status | cat /etc/passwd"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks command substitution" {
  # shellcheck disable=SC2016 # literal $(...) must reach the hook unexpanded
  run run_hook 'ghmem show $(rm -rf /)'
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks backtick substitution" {
  # shellcheck disable=SC2016 # literal `...` must reach the hook unexpanded
  run run_hook 'ghmem show `rm -rf /`'
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks output redirection" {
  run run_hook "ghmem status > /etc/passwd"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks input redirection" {
  run run_hook "ghmem status < /etc/passwd"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a newline-smuggled second command" {
  run run_hook "$(printf 'ghmem status\nrm -rf /')"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a leading-whitespace-disguised command" {
  run run_hook " ghmem status"
  [ "$status" -eq 2 ]
}

# The note a `route` carries is prose: spaces, apostrophes, backticks and
# parentheses all belong in it, and none of them survive the single-line
# grammar above. A quoted heredoc is the one shape that carries prose with
# zero expansion, so it is allowed as a whole.

@test "ghmem-only: allows a route whose note arrives by quoted heredoc" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic <<'EOF'\na note\nEOF")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows a heredoc note containing backticks and apostrophes" {
  # shellcheck disable=SC2016 # the whole point is that these reach ghmem unexpanded
  run run_hook "$(printf "ghmem route abc123 42 some-topic <<'EOF'\ndmaluka's ruling on \`b.Settings\` and \$HOME and \$(id)\nEOF")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows a multi-line heredoc note" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic <<'EOF'\nfirst line\n\nthird line\nEOF")"
  [ "$status" -eq 0 ]
}

# A heredoc carrying nothing is a malformed command whichever verdict it
# accompanies: it opened a note and then supplied none. Blocking it here is a
# grammar rule — it needs no knowledge of which verdict wants a note, which
# stays in `record_verdict`.

@test "ghmem-only: blocks a heredoc with no body at all" {
  run run_hook "$(printf "ghmem route abc123 42 no-signal <<'EOF'\nEOF")"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a heredoc whose body is only blank lines" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic <<'EOF'\n   \n\t\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: allows a heredoc command with a trailing newline" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic <<'EOF'\na note\nEOF\n")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: blocks a command smuggled after the heredoc terminator" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'EOF'\na note\nEOF\nrm -rf /")"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a heredoc body that closes early and reopens" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'EOF'\na note\nEOF\nrm -rf /\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unquoted heredoc delimiter" {
  # Unquoted means bash expands the body — exactly the corruption this guards.
  run run_hook "$(printf "ghmem route abc123 42 t <<EOF\na \`rm -rf /\` note\nEOF")"
  [ "$status" -eq 2 ]
}

# The delimiter is the writer's to pick. A fixed word would be a word no note
# may ever contain, and a note is prose about a corpus whose vocabulary nobody
# controls; picking a word your own note does not contain is the same
# guarantee, so every rule below anchors to whatever the opener named.

@test "ghmem-only: allows a delimiter of the writer's choosing" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'NOTE_END'\na note\nNOTE_END")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows a note naming a delimiter word it did not open with" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'NOTE_END'\na note about the heredoc whose EOF line ends it\nNOTE_END")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: blocks a body closed by a delimiter the opener did not name" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'NOTE_END'\na note\nEOF")"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a chosen delimiter appearing early in the body" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'NOTE_END'\na note\nNOTE_END\nrm -rf /\nNOTE_END")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an empty delimiter" {
  # An empty delimiter is closed by an empty line, so every blank line in a
  # note becomes a terminator and everything after one becomes command. It is
  # refused twice over — the opener demands a non-empty run, and a terminating
  # empty line could not reach the checks anyway, since reading the command
  # through `$(...)` strips the trailing newlines it is made of.
  run run_hook $'ghmem route abc123 42 t <<\'\'\na note\n\n'
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a delimiter containing whitespace" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'MY NOTE'\na note\nMY NOTE")"
  [ "$status" -eq 2 ]
}

# The next two close their body with exactly what their opener named, so the
# delimiter's own character class is the only rule left that can turn them
# down. Both would go through if it admitted the character they are about.

@test "ghmem-only: blocks a delimiter that closes its own quoting" {
  # A quote inside the delimiter ends the single-quoted span, and what the
  # shell then does with the rest of the line is not what the hook read.
  run run_hook "$(printf "ghmem route abc123 42 t <<'IT'S'\na note\nIT'S")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a delimiter carrying a shell metacharacter" {
  # Single quotes would in fact keep this inert — bash expands nothing in a
  # delimiter word. The class is narrow anyway so that the hook's safety is
  # visible in the pattern instead of resting on that argument.
  # shellcheck disable=SC2016 # the literal $(...) is the point
  run run_hook "$(printf 'ghmem route abc123 42 t <<%s\na note\n$(id)' "'\$(id)'")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a tab-stripping heredoc opener" {
  run run_hook "$(printf "ghmem route abc123 42 t <<-'EOF'\na note\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks anything trailing the heredoc opener on line one" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'EOF' ; rm -rf /\na note\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unterminated heredoc" {
  run run_hook "$(printf "ghmem route abc123 42 t <<'EOF'\na note")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unterminated heredoc that names the delimiter inline" {
  # Naming EOF mid-line is not closing it: only a line that *is* the
  # delimiter terminates the body, and it has to be the last one.
  run run_hook "$(printf "ghmem route abc123 42 t <<'EOF'\nthe EOF delimiter")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a heredoc opened by a non-ghmem command" {
  run run_hook "$(printf "cat <<'EOF'\na note\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: a block prints a reason to stderr, not stdout" {
  run --separate-stderr run_hook "rm -rf /"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

# Once the payload names a ghmem agent the hook knows whose call this is, so a
# payload it then cannot read is that agent's failure and blocks. It stops
# only those two agents; every other call is unaffected.
@test "ghmem-only: blocks a ghmem agent's call whose payload cannot be read" {
  for AGENT_TYPE in ghmem:ghmem-triage ghmem:ghmem-synthesize; do
    for body in \
      "{\"agent_type\": \"$AGENT_TYPE\"}" \
      "{\"agent_type\": \"$AGENT_TYPE\", \"tool_input\": {}}" \
      "{\"agent_type\": \"$AGENT_TYPE\", \"tool_input\": {\"command\": 42}}" \
      "{\"agent_type\": \"$AGENT_TYPE\", \"tool_input\": \"ghmem status\"}"; do
      run --separate-stderr bash "$HOOK" <<<"$body"
      [ "$status" -eq 2 ]
      [ -z "$output" ]
      [ -n "$stderr" ]
    done
  done
}

@test "ghmem-only: passes a malformed payload through when it names no ghmem agent" {
  for body in \
    '{"tool_input": {}}' \
    '{"agent_type": 5, "tool_input": {}}' \
    '{"agent_type": "general-purpose", "tool_input": {"command": 42}}' \
    '[]'; do
    run --separate-stderr bash "$HOOK" <<<"$body"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ -z "$stderr" ]
  done
}
