#!/usr/bin/env bats
# hooks/scripts/scope-packets-only.sh — the PreToolUse(Read) hook that scopes
# ghmem-triage's Read access to corpus/packets/, so a `packet-file:`
# line spoofed inside untrusted corpus content can't steer Read anywhere else.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/scope-packets-only.sh"

setup() {
  PROJECT_DIR="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT_DIR/corpus/packets" "$PROJECT_DIR/corpus/packets-old"
  export CLAUDE_PROJECT_DIR="$PROJECT_DIR"
}

run_hook() {
  jq -n --arg fp "$1" --arg cwd "$PROJECT_DIR" \
    '{tool_name: "Read", tool_input: {file_path: $fp}, cwd: $cwd}' \
    | bash "$HOOK"
}

@test "scope-packets-only: allows a relative path under corpus/packets/" {
  run run_hook "corpus/packets/AbCd1234.txt"
  [ "$status" -eq 0 ]
}

@test "scope-packets-only: allows an absolute path under corpus/packets/" {
  run run_hook "$PROJECT_DIR/corpus/packets/AbCd1234.txt"
  [ "$status" -eq 0 ]
}

@test "scope-packets-only: blocks a path outside corpus/packets/ entirely" {
  run run_hook "tools/ghmem/src/ghmem/triage.py"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "scope-packets-only: blocks a sibling dir that merely shares the prefix" {
  run run_hook "corpus/packets-old/foo.txt"
  [ "$status" -eq 2 ]
}

@test "scope-packets-only: blocks traversal that escapes corpus/packets/" {
  run run_hook "corpus/packets/../../../../etc/passwd"
  [ "$status" -eq 2 ]
}

@test "scope-packets-only: blocks an absolute path outside the project entirely" {
  run run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
}

@test "scope-packets-only: blocks a spoofed packet-file path a corpus item could name" {
  run run_hook "/Users/someone/.ssh/id_rsa"
  [ "$status" -eq 2 ]
}

@test "scope-packets-only: a block prints a reason to stderr, not stdout" {
  run --separate-stderr run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

@test "scope-packets-only: blocks the retired tools/ghmem/tmp/packets location" {
  mkdir -p "$PROJECT_DIR/tools/ghmem/tmp/packets"
  run run_hook "tools/ghmem/tmp/packets/AbCd1234.txt"
  [ "$status" -eq 2 ]
}

@test "scope-packets-only: allows a packet under a project path containing spaces" {
  PROJECT_DIR="$BATS_TEST_TMPDIR/my project"
  mkdir -p "$PROJECT_DIR/corpus/packets"
  export CLAUDE_PROJECT_DIR="$PROJECT_DIR"
  run run_hook "corpus/packets/AbCd1234.txt"
  [ "$status" -eq 0 ]
  run run_hook "$PROJECT_DIR/corpus/packets/../x.txt"
  [ "$status" -eq 2 ]
}
