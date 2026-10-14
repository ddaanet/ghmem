#!/usr/bin/env bats
# bin/ghmem — the launcher Claude Code puts on the Bash tool's PATH while the
# plugin is enabled. It runs the CLI from the `.venv` that
# hooks/scripts/build-venv.sh builds in the plugin's tree, and says why when
# there is none. Each test copies it into a throwaway tree, with a stub in
# place of the venv's `ghmem`.
bats_require_minimum_version 1.5.0

LAUNCHER="$BATS_TEST_DIRNAME/../bin/ghmem"

setup() {
  TREE="$BATS_TEST_TMPDIR/a plugin"
  TOOLS="$BATS_TEST_TMPDIR/tools"
  ELSEWHERE="$BATS_TEST_TMPDIR/elsewhere"
  mkdir -p "$TREE/bin" "$TOOLS" "$ELSEWHERE"
  cp "$LAUNCHER" "$TREE/bin/ghmem"
  # The launcher's `env bash` shebang needs bash on a PATH that is otherwise
  # only the plugin's bin/.
  ln -s "$BASH" "$TOOLS/bash"
  ROOT="$(cd -P "$TREE" && pwd)"
}

# Run `ghmem "$@"` by PATH lookup, from a directory outside the plugin. A
# test that expects an exit status of 126 or 127 sets WANT to it, so bats does
# not read it as a command not found.
run_ghmem() {
  cd "$ELSEWHERE"
  run ${WANT:+-$WANT} --separate-stderr env -i PATH="$TREE/bin:$TOOLS" ghmem "$@"
}

# A stub venv `ghmem` that prints each argument in angle brackets, its
# working directory, and exits with status 3.
stub_venv_ghmem() {
  mkdir -p "$TREE/.venv/bin"
  cat >"$TREE/.venv/bin/ghmem" <<'STUB'
#!/bin/sh
for arg in "$@"; do printf '<%s>\n' "$arg"; done
pwd -P
exit 3
STUB
  chmod +x "$TREE/.venv/bin/ghmem"
}

@test "bin/ghmem: runs the venv's ghmem with its arguments and exit status" {
  stub_venv_ghmem
  run_ghmem status --json
  [ "$status" -eq 3 ]
  [ "${lines[0]}" = "<status>" ]
  [ "${lines[1]}" = "<--json>" ]
  [ -z "$stderr" ]
}

@test "bin/ghmem: passes arguments with spaces, quotes and emptiness through intact" {
  stub_venv_ghmem
  run_ghmem "two words" 'say "hi"' '' '*'
  [ "${lines[0]}" = "<two words>" ]
  [ "${lines[1]}" = '<say "hi">' ]
  [ "${lines[2]}" = "<>" ]
  [ "${lines[3]}" = "<*>" ]
  [ "${#lines[@]}" -eq 5 ]
}

@test "bin/ghmem: leaves the caller's working directory alone" {
  stub_venv_ghmem
  run_ghmem
  [ "${lines[0]}" = "$(cd -P "$ELSEWHERE" && pwd)" ]
}

@test "bin/ghmem: names .venv and .venv-build.log on stderr, exit 127, without a venv" {
  WANT=127 run_ghmem status
  [ "$status" -eq 127 ]
  [ -z "$output" ]
  [[ "$stderr" == *"$ROOT/.venv"* ]]
  [[ "$stderr" == *"$ROOT/.venv-build.log"* ]]
}

@test "bin/ghmem: reports the same when .venv exists with no ghmem in it" {
  mkdir -p "$TREE/.venv/bin"
  WANT=127 run_ghmem status
  [ "$status" -eq 127 ]
  [[ "$stderr" == *.venv-build.log* ]]
}
