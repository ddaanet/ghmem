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

# AGENT_TYPE picks the agent; setting it empty drops the key the way a
# main-thread payload does.
payload() {
  jq -n --arg fp "$1" --arg cwd "$PROJECT_DIR" \
    --arg agent "${AGENT_TYPE-ghmem:ghmem-triage}" \
    '{tool_name: "Read", tool_input: {file_path: $fp}, cwd: $cwd}
     + if $agent == "" then {} else {agent_type: $agent} end'
}

run_hook() {
  payload "$1" | bash "$HOOK"
}

@test "scope-packets-only: passes any read through, silently, when no agent is named" {
  AGENT_TYPE=""
  run --separate-stderr run_hook "/etc/passwd"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

# ghmem-synthesize's Read is scope-docs-design's to hold, not this hook's;
# the bare name is a project's own definition, not the plugin's.
@test "scope-packets-only: passes any read through for an agent other than ghmem-triage" {
  for AGENT_TYPE in ghmem:ghmem-synthesize general-purpose ghmem-triage \
    other:ghmem-triage; do
    run run_hook "/etc/passwd"
    [ "$status" -eq 0 ]
  done
}

# A hook that cannot decide lets the call through, silently: refusing would
# stop Read in every session that enables the plugin, and a non-2 exit with
# stderr shows as a hook error on every call. The missing dependency is
# reported once, by check-deps.sh at SessionStart. `realpath -m` is GNU-only,
# which makes the second case a real one.
@test "scope-packets-only: passes through, silently, when the payload is not JSON" {
  run --separate-stderr bash "$HOOK" <<<"not json"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "scope-packets-only: passes through, silently, when realpath fails" {
  mkdir "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/sh\necho realpath-broke >&2\nexit 1\n' \
    >"$BATS_TEST_TMPDIR/bin/realpath"
  chmod +x "$BATS_TEST_TMPDIR/bin/realpath"
  body="$(payload "/etc/passwd")"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH" run --separate-stderr bash "$HOOK" <<<"$body"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
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
