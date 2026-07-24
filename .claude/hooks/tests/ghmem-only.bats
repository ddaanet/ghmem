#!/usr/bin/env bats
# .claude/hooks/ghmem-only.sh — the PreToolUse(Bash) allowlist hook that keeps
# ghmem-triage/ghmem-synthesize on their only sanctioned data path. Spec:
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md §The allowlist hook.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../ghmem-only.sh"

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

@test "ghmem-only: a block prints a reason to stderr, not stdout" {
  run --separate-stderr run_hook "rm -rf /"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}
