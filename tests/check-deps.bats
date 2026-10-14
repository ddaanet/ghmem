#!/usr/bin/env bats
# hooks/scripts/check-deps.sh — the SessionStart hook that names the one
# dependency of the containment hooks. Those hooks fail open, silently, when
# there is no python3 or it cannot run the plugin's hook module, so this
# report is the only place that shows.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/check-deps.sh"

# Captured before any test narrows PATH, to read the hook's output with and to
# find the python3 the happy path must reach.
JQ="$(command -v jq)"
PYTHON_DIR="$(dirname "$(command -v python3)")"

setup() {
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir "$BIN"
}

# PATH is replaced, not prepended, so a tool not linked into BIN is missing.
run_hook() {
  run --separate-stderr env -i PATH="$BIN" "$BASH" "$HOOK" <<<'{}'
}

field() {
  "$JQ" -er "$1" <<<"$output"
}

# Both channels carry the warning, and it names the dependency and the agents
# not to dispatch.
assert_warns() {
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [ "$(field .hookSpecificOutput.hookEventName)" = SessionStart ]
  [[ "$(field .systemMessage)" == *python3* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *python3* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-triage* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-synthesize* ]]
}

@test "check-deps: says nothing when python3 can run the hook module" {
  run --separate-stderr env -i PATH="$PYTHON_DIR" "$BASH" "$HOOK" <<<'{}'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "check-deps: names python3 to both user and agent when it is missing" {
  run_hook
  assert_warns
}

@test "check-deps: names python3 when it is on PATH but fails to start" {
  printf '#!/bin/sh\nexit 127\n' >"$BIN/python3"
  chmod +x "$BIN/python3"
  run_hook
  assert_warns
}

# python3 exits 2 on a script it cannot open, so the probe must read that as
# a failure and warn.
@test "check-deps: names python3 when the hook module is missing from the tree" {
  mkdir -p "$BATS_TEST_TMPDIR/tree/hooks"
  cp -R "$BATS_TEST_DIRNAME/../hooks/scripts" "$BATS_TEST_TMPDIR/tree/hooks/scripts"
  run --separate-stderr env -i PATH="$PYTHON_DIR" "$BASH" \
    "$BATS_TEST_TMPDIR/tree/hooks/scripts/check-deps.sh" <<<'{}'
  assert_warns
}
