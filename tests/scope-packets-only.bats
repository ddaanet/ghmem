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
# reported once, by check-deps.sh at SessionStart.
@test "scope-packets-only: passes through, silently, when the payload is not JSON" {
  run --separate-stderr bash "$HOOK" <<<"not json"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "scope-packets-only: passes through, silently, when ghmem-hook is not on PATH" {
  mkdir "$BATS_TEST_TMPDIR/empty"
  body="$(payload "/etc/passwd")"
  run --separate-stderr env -i PATH="$BATS_TEST_TMPDIR/empty" "$BASH" "$HOOK" <<<"$body"
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

# macOS ships a realpath without GNU's `-m`. The scope must hold there, not
# merely fail safe: the hook resolves paths itself rather than asking one.
@test "scope-packets-only: holds its scope where realpath lacks -m, as on macOS" {
  mkdir "$BATS_TEST_TMPDIR/bin"
  # shellcheck disable=SC2016  # the stub's own $1, expanded when it runs
  printf '#!/bin/sh\n[ "$1" = -m ] && exit 1\nexec %s "$@"\n' \
    "$(command -v realpath)" >"$BATS_TEST_TMPDIR/bin/realpath"
  chmod +x "$BATS_TEST_TMPDIR/bin/realpath"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  run run_hook "corpus/packets/AbCd1234.txt"
  [ "$status" -eq 0 ]
  run run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
  run run_hook "$PROJECT_DIR/corpus/packets/../../x.txt"
  [ "$status" -eq 2 ]
}

# Once the payload names ghmem-triage the hook knows whose call this is, so a payload
# or a path it then cannot resolve is that agent's failure and blocks. It stops
# only that agent; every other call is unaffected.
@test "scope-packets-only: blocks its agent's call whose payload or path cannot be resolved" {
  for body in \
    '{"agent_type": "ghmem:ghmem-triage", "cwd": "/"}' \
    '{"agent_type": "ghmem:ghmem-triage", "cwd": "/", "tool_input": {}}' \
    '{"agent_type": "ghmem:ghmem-triage", "cwd": "/", "tool_input": {"file_path": 42}}' \
    '{"agent_type": "ghmem:ghmem-triage", "cwd": "/", "tool_input": {"file_path": "a\u0000b"}}' \
    '{"agent_type": "ghmem:ghmem-triage", "tool_input": {"file_path": "x.txt"}}'; do
    run --separate-stderr env -u CLAUDE_PROJECT_DIR bash "$HOOK" <<<"$body"
    [ "$status" -eq 2 ]
    [ -z "$output" ]
    [ -n "$stderr" ]
  done
}

@test "scope-packets-only: passes a malformed payload through when it is not its agent's" {
  for body in \
    '{"tool_input": {}}' \
    '{"agent_type": 5, "tool_input": {}}' \
    '{"agent_type": "general-purpose", "tool_input": {"file_path": 42}}'; do
    run --separate-stderr bash "$HOOK" <<<"$body"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ -z "$stderr" ]
  done
}
