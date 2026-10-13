#!/usr/bin/env bats
# hooks/scripts/check-deps.sh — the SessionStart hook that names any missing
# dependency of the containment hooks. Those hooks fail open, silently, when
# they cannot run, so this report is the only place a missing one shows.
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

link() {
  ln -s "$(command -v "$1")" "$BIN/$1"
}

# A realpath that exists but rejects `-m`, as BSD and macOS ship it.
bsd_realpath() {
  # shellcheck disable=SC2016  # the stub's own $1, expanded when it runs
  printf '#!/bin/sh\n[ "$1" = -m ] && exit 1\necho "$1"\n' >"$BIN/realpath"
  chmod +x "$BIN/realpath"
}

field() {
  "$JQ" -er "$1" <<<"$output"
}

@test "check-deps: says nothing when jq and GNU realpath are both present" {
  link jq
  link realpath
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "check-deps: names jq to both user and agent when it is missing" {
  link realpath
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [ "$(field .hookSpecificOutput.hookEventName)" = SessionStart ]
  [[ "$(field .systemMessage)" == *jq* ]]
  [[ "$(field .systemMessage)" != *realpath* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *jq* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-triage* ]]
}

@test "check-deps: names realpath when it lacks GNU's -m" {
  link jq
  bsd_realpath
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [[ "$(field .systemMessage)" == *realpath* ]]
  [[ "$(field .systemMessage)" != *jq* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *realpath* ]]
}

@test "check-deps: names both when both are missing" {
  run_hook
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  [[ "$(field .systemMessage)" == *jq*realpath* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *jq*realpath* ]]
}
