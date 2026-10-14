#!/usr/bin/env bats
# hooks/scripts/check-deps.sh — the SessionStart hook that names the one
# dependency of the containment hooks. Those hooks fail open, silently, when
# `ghmem-hook` is not on PATH, so this report is the only place that shows.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/check-deps.sh"

# Captured before any test narrows PATH, to read the hook's output with.
JQ="$(command -v jq)"

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

@test "check-deps: says nothing when ghmem-hook is on PATH" {
  printf '#!/bin/sh\n' >"$BIN/ghmem-hook"
  chmod +x "$BIN/ghmem-hook"
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "check-deps: names ghmem-hook when it is on PATH but fails to start" {
  printf '#!/bin/sh\nexit 127\n' >"$BIN/ghmem-hook"
  chmod +x "$BIN/ghmem-hook"
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [[ "$(field .systemMessage)" == *ghmem-hook* ]]
}

@test "check-deps: names ghmem-hook to both user and agent when it is missing" {
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [ "$(field .hookSpecificOutput.hookEventName)" = SessionStart ]
  [[ "$(field .systemMessage)" == *ghmem-hook* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem-hook* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-triage* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-synthesize* ]]
}
