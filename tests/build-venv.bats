#!/usr/bin/env bats
# hooks/scripts/build-venv.sh — the SessionStart hook that builds the
# plugin's own `.venv`, which bin/ghmem runs. Every test copies the script
# into a throwaway tree, so the hook's root is that tree and the repository's
# own `.venv` is never touched, and runs it with a PATH holding only stub
# tools: the real uv and pip are never called.
bats_require_minimum_version 1.5.0

HOOK="$BATS_TEST_DIRNAME/../hooks/scripts/build-venv.sh"

JQ="$(command -v jq)"

setup() {
  BIN="$BATS_TEST_TMPDIR/bin"
  TOOLS="$BATS_TEST_TMPDIR/tools"
  CALLS="$BATS_TEST_TMPDIR/calls"
  mkdir -p "$BIN" "$TOOLS"
  : >"$CALLS"
  make_tree "$BATS_TEST_TMPDIR/tree"
  # The system tools the hook uses, linked in so that PATH can be replaced
  # and a tool not linked or stubbed (uv, python3) is missing.
  local tool
  for tool in find mkdir rmdir rm touch sleep chmod cp; do
    ln -s "$(command -v "$tool")" "$TOOLS/$tool"
  done
}

# TREE is the plugin root the hook will find from its own location; ROOT is
# the same path with symlinks resolved, which is how the hook spells it.
make_tree() {
  TREE="$1"
  mkdir -p "$TREE/hooks/scripts"
  cp "$HOOK" "$TREE/hooks/scripts/build-venv.sh"
  ROOT="$(cd -P "$TREE" && pwd)"
}

# PATH is replaced, not prepended. The hook's stdin is the SessionStart
# payload.
run_hook() {
  run --separate-stderr env -i PATH="$BIN:$TOOLS" "$BASH" \
    "$TREE/hooks/scripts/build-venv.sh" <<<'{}'
}

field() {
  "$JQ" -er "$1" <<<"$output"
}

# A stub `ghmem` that exits with status $2, at path $1.
stub_ghmem() {
  mkdir -p "${1%/*}"
  printf '#!/bin/sh\nexit %s\n' "$2" >"$1"
  chmod +x "$1"
}

# A stub uv that records its arguments and working directory. `sync` leaves
# a working `.venv/bin/ghmem`, as a real one would, unless the mode is "empty"
# (exits 0 and builds nothing) or "fail" (exits 1).
stub_uv() {
  local mode="${1:-ok}"
  cat >"$BIN/uv" <<EOF
#!/bin/sh
echo "uv \$* (in \$(pwd -P))" >>"$CALLS"
case "$mode" in
  fail) exit 1 ;;
  empty) exit 0 ;;
esac
mkdir -p .venv/bin
printf '#!/bin/sh\nexit 0\n' >.venv/bin/ghmem
chmod +x .venv/bin/ghmem
EOF
  chmod +x "$BIN/uv"
}

# A stub python3 reporting version $2 (default 3.14), so the hook's `-c`
# version probe passes for 3.14 and fails for 3.13: it exits 0 or 1 on the
# probe and prints the version on the message query. `-m venv DIR` makes
# DIR/bin/pip, which records its arguments and makes DIR/bin/ghmem only on
# the install of the tree itself (no `-r`), so a working ghmem exists only if
# the hook went on to run that install. Every call is recorded. Mode "fail"
# makes `-m venv` fail; mode "pipfail" makes `pip install -r` fail.
stub_python3() {
  local mode="${1:-ok}" version="${2:-3.14}"
  cat >"$BATS_TEST_TMPDIR/pip-template" <<EOF
#!/bin/sh
echo "pip \$*" >>"$CALLS"
case " \$* " in
  *" -r "*) [ "$mode" = pipfail ] && exit 1; exit 0 ;;
esac
printf '#!/bin/sh\nexit 0\n' >"\${0%/*}/ghmem"
chmod +x "\${0%/*}/ghmem"
EOF
  cat >"$BIN/python3" <<EOF
#!/bin/sh
echo "python3 \$*" >>"$CALLS"
if [ "\$1" = -c ]; then
  case "\$2" in
    *sys.exit*) [ "${version#3.}" -ge 14 ]; exit \$? ;;
    *) echo "$version"; exit 0 ;;
  esac
fi
[ "\$1 \$2" = "-m venv" ] || exit 2
[ "$mode" = fail ] && exit 1
mkdir -p "\$3/bin"
cp "$BATS_TEST_TMPDIR/pip-template" "\$3/bin/pip"
chmod +x "\$3/bin/pip"
EOF
  chmod +x "$BIN/python3"
}

# The output is one JSON object whose systemMessage contains $1, and nothing
# is on stderr.
assert_message() {
  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
  "$JQ" -e . <<<"$output" >/dev/null
  [[ "$(field .systemMessage)" == *"$1"* ]]
}

# A failure also tells the agent, by name, not to dispatch either ghmem agent.
assert_agent_told() {
  [ "$(field .hookSpecificOutput.hookEventName)" = SessionStart ]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-triage* ]]
  [[ "$(field .hookSpecificOutput.additionalContext)" == *ghmem:ghmem-synthesize* ]]
}

assert_silent() {
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -z "$stderr" ]
}

@test "build-venv: says nothing and builds nothing when ghmem already runs" {
  stub_ghmem "$TREE/.venv/bin/ghmem" 0
  stub_uv
  stub_python3
  run_hook
  assert_silent
  [ ! -s "$CALLS" ]
  [ ! -e "$TREE/.venv.lock" ]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  [ ! -e "$TREE/.venv-build.log" ]
}

@test "build-venv: builds nothing when another ghmem is on PATH ahead of the plugin's" {
  stub_ghmem "$BIN/ghmem" 0
  stub_uv
  stub_python3
  run_hook
  assert_silent
  [ ! -s "$CALLS" ]
  [ ! -e "$TREE/.venv" ]
  [ ! -e "$TREE/.venv-build.log" ]
}

@test "build-venv: still builds when the only ghmem on PATH is the plugin's own launcher" {
  stub_ghmem "$TREE/bin/ghmem" 127
  ln -s "$TREE/bin/ghmem" "$BIN/ghmem"
  stub_uv
  run_hook
  [ "$status" -eq 0 ]
  [ -x "$TREE/.venv/bin/ghmem" ]
  grep -q 'sync --frozen --no-dev' "$CALLS"
}

@test "build-venv: builds with uv sync --frozen --no-dev in the plugin root" {
  stub_uv
  run_hook
  assert_message "ghmem: built its CLI environment"
  [ -x "$TREE/.venv/bin/ghmem" ]
  [ -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  [ "$(cat "$CALLS")" = "uv sync --frozen --no-dev --quiet (in $ROOT)" ]
  [ "$("$JQ" 'has("hookSpecificOutput")' <<<"$output")" = false ]
}

@test "build-venv: replaces a broken venv this hook built" {
  stub_ghmem "$TREE/.venv/bin/ghmem" 1
  touch "$TREE/.venv/.built-by-ghmem-plugin"
  stub_uv
  run_hook
  assert_message "ghmem: built its CLI environment"
  "$TREE/.venv/bin/ghmem" --version
  [ "$(grep -c '^uv sync' "$CALLS")" -eq 1 ]
}

@test "build-venv: falls back to python3 -m venv and the pinned requirements when uv is absent" {
  stub_python3
  run_hook
  assert_message "ghmem: built its CLI environment"
  [ -x "$TREE/.venv/bin/ghmem" ]
  [ -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  grep -Fxq "python3 -m venv $ROOT/.venv" "$CALLS"
  # The pinned requirements first, then the tree without its dependencies.
  [ "$(grep '^pip ' "$CALLS")" = "pip install --quiet -r $ROOT/requirements.txt
pip install --quiet --no-deps $ROOT" ]
}

@test "build-venv: refuses the fallback when python3 is older than 3.14" {
  stub_python3 ok 3.13
  run_hook
  assert_message "could not build"
  assert_agent_told
  grep -q '3\.14' "$TREE/.venv-build.log"
  grep -q '3\.13' "$TREE/.venv-build.log"
  [ "$(grep -c '^python3 -m venv' "$CALLS")" -eq 0 ]
  [ ! -e "$TREE/.venv" ]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
}

@test "build-venv: does not install the tree when the pinned requirements fail" {
  stub_python3 pipfail
  run_hook
  assert_message "could not build"
  assert_agent_told
  grep -q "^pip install --quiet -r $ROOT/requirements.txt\$" "$CALLS"
  [ "$(grep -c -- '--no-deps' "$CALLS")" -eq 0 ]
  [ ! -x "$TREE/.venv/bin/ghmem" ]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  [ -s "$TREE/.venv-build.log" ]
}

@test "build-venv: falls back when uv exits 0 but leaves no working ghmem" {
  stub_uv empty
  stub_python3
  run_hook
  assert_message "ghmem: built its CLI environment"
  grep -q '^uv sync' "$CALLS"
  grep -q '^pip install' "$CALLS"
  [ -e "$TREE/.venv/.built-by-ghmem-plugin" ]
}

@test "build-venv: tells user and agent when uv and python3 both fail" {
  stub_uv fail
  stub_python3 fail
  run_hook
  assert_message "could not build"
  assert_agent_told
  [ -s "$TREE/.venv-build.log" ]
  [[ "$(field .systemMessage)" == *.venv-build.log* ]]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  grep -q '^uv sync' "$CALLS"
  grep -q '^python3 -m venv' "$CALLS"
}

@test "build-venv: tells user and agent when neither uv nor python3 exists" {
  run_hook
  assert_message "could not build"
  assert_agent_told
  [ -s "$TREE/.venv-build.log" ]
  grep -q 'no python3' "$TREE/.venv-build.log"
  [ ! -e "$TREE/.venv.lock" ]
}

@test "build-venv: leaves a broken venv it did not build alone" {
  stub_ghmem "$TREE/.venv/bin/ghmem" 1
  stub_uv
  stub_python3
  run_hook
  assert_message "left alone"
  assert_agent_told
  [ -x "$TREE/.venv/bin/ghmem" ]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
  [ ! -s "$CALLS" ]
  [ ! -e "$TREE/.venv.lock" ]
}

@test "build-venv: takes over a lock older than ten minutes" {
  mkdir "$TREE/.venv.lock"
  touch -t 200001010000 "$TREE/.venv.lock"
  stub_uv
  local started=$SECONDS
  run_hook
  # Not the four minutes a live lock is waited on.
  [ $((SECONDS - started)) -lt 30 ]
  assert_message "ghmem: built its CLI environment"
  [ "$(grep -c '^uv sync' "$CALLS")" -eq 1 ]
  [ -e "$TREE/.venv/.built-by-ghmem-plugin" ]
}

@test "build-venv: waits on a fresh lock and exits silently once the venv is ready" {
  mkdir "$TREE/.venv.lock"
  stub_uv
  (
    sleep 1
    stub_ghmem "$TREE/.venv/bin/ghmem" 0
    rmdir "$TREE/.venv.lock"
  ) >/dev/null 2>&1 3>&- &
  local started=$SECONDS
  run_hook
  wait
  assert_silent
  # It waited: the venv appeared a second in, and SECONDS counts whole seconds.
  [ $((SECONDS - started)) -ge 1 ]
  [ ! -s "$CALLS" ]
  [ ! -e "$TREE/.venv/.built-by-ghmem-plugin" ]
}

@test "build-venv: builds for itself when the lock it waited on is dropped with no venv" {
  mkdir "$TREE/.venv.lock"
  stub_uv
  (
    sleep 1
    rmdir "$TREE/.venv.lock"
  ) >/dev/null 2>&1 3>&- &
  run_hook
  wait
  assert_message "ghmem: built its CLI environment"
  [ "$(grep -c '^uv sync' "$CALLS")" -eq 1 ]
}

@test "build-venv: removes its lock after a build and after a failure" {
  stub_uv
  run_hook
  assert_message "ghmem: built its CLI environment"
  [ ! -e "$TREE/.venv.lock" ]
  rm -rf "$TREE/.venv"
  rm "$BIN/uv"
  run_hook
  assert_message "could not build"
  [ ! -e "$TREE/.venv.lock" ]
}

@test "build-venv: JSON stays valid when the plugin path has a space and a quote" {
  make_tree "$BATS_TEST_TMPDIR/a tree \"q\""
  stub_uv
  run_hook
  assert_message "ghmem: built its CLI environment"
  [[ "$(field .systemMessage)" == *"a tree \"q\"/.venv"* ]]
}
