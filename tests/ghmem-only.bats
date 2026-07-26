#!/usr/bin/env bats
# hooks/scripts/ghmem-only.sh — the PreToolUse(Bash) allowlist hook that keeps
# ghmem-triage/ghmem-synthesize on their only sanctioned data path. Spec:
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist hook.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/ghmem-only.sh"

# The hook only reads .tool_input.command; the rest of the PreToolUse
# envelope is irrelevant to it, so tests omit it.
run_hook() {
  jq -n --arg cmd "$1" '{tool_name: "Bash", tool_input: {command: $cmd}}' | bash "$HOOK"
}

@test "ghmem-only: allows a bare ghmem invocation with no arguments" {
  run run_hook "ghmem"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows ghmem with simple subcommand args" {
  run run_hook "ghmem triage"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows ghmem route with token, number, verdict, note" {
  run run_hook "ghmem route abc123 42 softwrapcolumn some-note"
  [ "$status" -eq 0 ]
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
# grammar above. A quoted heredoc with a fixed delimiter is the one shape
# that carries prose with zero expansion, so it is allowed as a whole.

@test "ghmem-only: allows a route whose note arrives by quoted heredoc" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic --note <<'GHMEMNOTE'\na note\nGHMEMNOTE")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows a heredoc note containing backticks and apostrophes" {
  # shellcheck disable=SC2016 # the whole point is that these reach ghmem unexpanded
  run run_hook "$(printf "ghmem route abc123 42 some-topic --note <<'GHMEMNOTE'\ndmaluka's ruling on \`b.Settings\` and \$HOME and \$(id)\nGHMEMNOTE")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: allows a multi-line heredoc note" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic --note <<'GHMEMNOTE'\nfirst line\n\nthird line\nGHMEMNOTE")"
  [ "$status" -eq 0 ]
}

# A heredoc carrying nothing is a malformed command whichever verdict it
# accompanies: it opened a note and then supplied none. Blocking it here is a
# grammar rule — it needs no knowledge of which verdict wants a note, which
# stays in `record_verdict`.

@test "ghmem-only: blocks a heredoc with no body at all" {
  run run_hook "$(printf "ghmem route abc123 42 no-signal --note <<'GHMEMNOTE'\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a heredoc whose body is only blank lines" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic --note <<'GHMEMNOTE'\n   \n\t\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: allows a heredoc command with a trailing newline" {
  run run_hook "$(printf "ghmem route abc123 42 some-topic --note <<'GHMEMNOTE'\na note\nGHMEMNOTE\n")"
  [ "$status" -eq 0 ]
}

@test "ghmem-only: blocks a command smuggled after the heredoc terminator" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'GHMEMNOTE'\na note\nGHMEMNOTE\nrm -rf /")"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "ghmem-only: blocks a heredoc body that closes early and reopens" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'GHMEMNOTE'\na note\nGHMEMNOTE\nrm -rf /\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unquoted heredoc delimiter" {
  # Unquoted means bash expands the body — exactly the corruption this guards.
  run run_hook "$(printf "ghmem route abc123 42 t --note <<GHMEMNOTE\na \`rm -rf /\` note\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a heredoc delimiter other than GHMEMNOTE" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'EOF'\na note\nEOF")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a tab-stripping heredoc opener" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<-'GHMEMNOTE'\na note\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks anything trailing the heredoc opener on line one" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'GHMEMNOTE' ; rm -rf /\na note\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unterminated heredoc" {
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'GHMEMNOTE'\na note")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks an unterminated heredoc that names the delimiter inline" {
  # Naming GHMEMNOTE mid-line is not closing it: only a line that *is* the
  # delimiter terminates the body, and it has to be the last one.
  run run_hook "$(printf "ghmem route abc123 42 t --note <<'GHMEMNOTE'\nthe GHMEMNOTE delimiter")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: blocks a heredoc opened by a non-ghmem command" {
  run run_hook "$(printf "cat --note <<'GHMEMNOTE'\na note\nGHMEMNOTE")"
  [ "$status" -eq 2 ]
}

@test "ghmem-only: a block prints a reason to stderr, not stdout" {
  run --separate-stderr run_hook "rm -rf /"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}
