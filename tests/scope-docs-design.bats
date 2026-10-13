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
# run from the project root. AGENT_TYPE picks the agent; setting it empty
# drops the key the way a main-thread payload does.
payload() {
  jq -n --arg fp "$1" --arg cwd "$PROJECT_DIR" \
    --arg agent "${AGENT_TYPE-ghmem:ghmem-synthesize}" \
    '{tool_name: "Write", tool_input: {file_path: $fp}, cwd: $cwd}
     + if $agent == "" then {} else {agent_type: $agent} end'
}

run_hook() {
  payload "$1" | bash "$HOOK"
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

# Any exit but 2 reads as "not a block", so a hook that cannot decide has to
# refuse. `realpath -m` is GNU-only, which makes the second case a real one.
@test "scope-docs-design: blocks when the payload is not JSON" {
  run bash "$HOOK" <<<"not json"
  [ "$status" -eq 2 ]
}

@test "scope-docs-design: blocks when realpath fails" {
  mkdir "$BATS_TEST_TMPDIR/bin"
  printf '#!/bin/sh\nexit 1\n' >"$BATS_TEST_TMPDIR/bin/realpath"
  chmod +x "$BATS_TEST_TMPDIR/bin/realpath"
  body="$(payload "docs/design/softwrapcolumn.md")"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH" run bash "$HOOK" <<<"$body"
  [ "$status" -eq 2 ]
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
