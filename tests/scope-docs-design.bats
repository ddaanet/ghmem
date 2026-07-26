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
# Write|Edit); cwd mirrors the real invocation, where agent Bash/tool calls
# run from the project root.
run_hook() {
  jq -n --arg fp "$1" --arg cwd "$PROJECT_DIR" \
    '{tool_name: "Write", tool_input: {file_path: $fp}, cwd: $cwd}' \
    | bash "$HOOK"
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
