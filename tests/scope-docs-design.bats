#!/usr/bin/env bats
# hooks/scripts/scope-docs-design.sh — the PreToolUse(Write|Edit) hook that
# scopes ghmem-synthesize's writes to docs/design/. Spec:
# docs/superpowers/specs/2026-07-24-ghmem-agents-design.md
# §The allowlist hook (write-path scope).
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/scope-docs-design.sh"

setup() {
  PROJECT_DIR="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT_DIR/docs/design" "$PROJECT_DIR/docs/design-old"
  export CLAUDE_PROJECT_DIR="$PROJECT_DIR"
}

# tool_name is irrelevant to the hook (the matcher already restricted it to
# Read|Write|Edit, and all three name their target as file_path); cwd mirrors
# the real invocation, where agent Bash/tool calls run from the project root.
# AGENT_TYPE picks the agent; setting it empty drops the key the way a
# main-thread payload does.
payload() {
  jq -n --arg fp "$1" --arg cwd "$PROJECT_DIR" \
    --arg agent "${AGENT_TYPE-ghmem:ghmem-synthesize}" \
    --arg tool "${TOOL_NAME-Write}" \
    '{tool_name: $tool, tool_input: {file_path: $fp}, cwd: $cwd}
     + if $agent == "" then {} else {agent_type: $agent} end'
}

run_hook() {
  payload "$1" | bash "$HOOK"
}

# The agent reads untrusted text and writes docs that get committed, so an
# unscoped Read is a way out for any file on the machine.
@test "scope-docs-design: holds a Read to docs/design/ and says so" {
  TOOL_NAME=Read
  run run_hook "docs/design/softwrapcolumn.md"
  [ "$status" -eq 0 ]
  run --separate-stderr run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
  [[ "$stderr" == *Read* ]]
}

@test "scope-docs-design: passes any write through, silently, when no agent is named" {
  AGENT_TYPE=""
  run --separate-stderr run_hook "/etc/passwd"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

# The bare name is a project's own definition, not the plugin's.
@test "scope-docs-design: passes any write through for an agent other than ghmem-synthesize" {
  for AGENT_TYPE in general-purpose ghmem-synthesize other:ghmem-synthesize \
    ghmem:ghmem-synthesize-2; do
    run run_hook "/etc/passwd"
    [ "$status" -eq 0 ]
  done
}

# A hook that cannot decide lets the call through, silently: refusing would
# stop Read, Write and Edit in every session that enables the plugin, and a
# non-2 exit with stderr shows as a hook error on every call. The missing
# dependency is reported once, by check-deps.sh at SessionStart.
@test "scope-docs-design: passes through, silently, when the payload is not JSON" {
  run --separate-stderr bash "$HOOK" <<<"not json"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "scope-docs-design: passes through, silently, when ghmem-hook is not on PATH" {
  mkdir "$BATS_TEST_TMPDIR/empty"
  body="$(payload "/etc/passwd")"
  run --separate-stderr env -i PATH="$BATS_TEST_TMPDIR/empty" "$BASH" "$HOOK" <<<"$body"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "scope-docs-design: allows a relative path under docs/design/" {
  run run_hook "docs/design/softwrapcolumn.md"
  [ "$status" -eq 0 ]
}

@test "scope-docs-design: allows an absolute path under docs/design/" {
  run run_hook "$PROJECT_DIR/docs/design/softwrapcolumn.md"
  [ "$status" -eq 0 ]
}

@test "scope-docs-design: allows a nested subdirectory under docs/design/" {
  run run_hook "docs/design/archive/old-topic.md"
  [ "$status" -eq 0 ]
}

@test "scope-docs-design: blocks a path outside docs/design/ entirely" {
  run run_hook "docs/other.md"
  [ "$status" -eq 2 ]
  [ -n "$output" ]
}

@test "scope-docs-design: blocks a sibling dir that merely shares the prefix" {
  run run_hook "docs/design-old/foo.md"
  [ "$status" -eq 2 ]
}

@test "scope-docs-design: blocks traversal that escapes docs/design/" {
  run run_hook "docs/design/../../etc/passwd"
  [ "$status" -eq 2 ]
}

@test "scope-docs-design: blocks an absolute path outside the project entirely" {
  run run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
}

@test "scope-docs-design: a block prints a reason to stderr, not stdout" {
  run --separate-stderr run_hook "docs/other.md"
  [ "$status" -eq 2 ]
  [ -z "$output" ]
  [ -n "$stderr" ]
}

# macOS ships a realpath without GNU's `-m`. The scope must hold there, not
# merely fail safe: the hook resolves paths itself rather than asking one.
@test "scope-docs-design: holds its scope where realpath lacks -m, as on macOS" {
  mkdir "$BATS_TEST_TMPDIR/bin"
  # shellcheck disable=SC2016  # the stub's own $1, expanded when it runs
  printf '#!/bin/sh\n[ "$1" = -m ] && exit 1\nexec %s "$@"\n' \
    "$(command -v realpath)" >"$BATS_TEST_TMPDIR/bin/realpath"
  chmod +x "$BATS_TEST_TMPDIR/bin/realpath"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  run run_hook "docs/design/softwrapcolumn.md"
  [ "$status" -eq 0 ]
  run run_hook "/etc/passwd"
  [ "$status" -eq 2 ]
  run run_hook "$PROJECT_DIR/docs/design/../../x.txt"
  [ "$status" -eq 2 ]
}
