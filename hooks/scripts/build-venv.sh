#!/usr/bin/env bash
# SessionStart hook: builds the plugin's CLI environment, `.venv` in the
# plugin's own tree, so the `ghmem` command in bin/ has its dependencies
# without anything installed beforehand. The tree is a per-version cache
# directory, so a plugin update gets a fresh build and nothing is migrated.
#
# `uv sync --frozen --no-dev` builds it from uv.lock; where uv is missing or
# fails, `python3 -m venv` and `pip install -r requirements.txt` (pinned from
# uv.lock by the gate) stand in, slower and needing a python3 of 3.14 or
# newer. An environment that already runs `ghmem --version` is left alone and
# the hook prints nothing, as does a session where another `ghmem` is already
# on PATH ahead of the plugin's (the plugin's bin/ comes last, so a build
# would go unused), and one this hook did not build (a development tree's own
# `.venv`, with no marker file) is never removed. A build says so once on
# `systemMessage`; a failure says why to the user, and to the agent on
# `additionalContext`, since it is the agent that dispatches ghmem's and they
# call `ghmem` by name.
#
# Several sessions can start at once in one tree, so the build takes a lock
# directory; a lock older than ten minutes is a build that died and is taken
# over. The containment hooks do not depend on this: they need only python3.
set -uo pipefail

root="$(CDPATH='' cd -P -- "${0%/*}/../.." && pwd)"
venv="$root/.venv"
lock="$root/.venv.lock"
marker="$venv/.built-by-ghmem-plugin"
log="$root/.venv-build.log"

main() {
  ready && exit 0
  provided_elsewhere && exit 0
  acquire
  # Another session may have finished the build while this one waited.
  ready && exit 0
  if [[ -e $venv && ! -e $marker ]]; then
    # A development tree's own environment, not this hook's to rebuild.
    say "ghmem: $venv exists but does not run ghmem, and this hook did not build it, so it is left alone." \
      "The ghmem command is unavailable in this session ($venv is broken). Do not dispatch ghmem:ghmem-triage or ghmem:ghmem-synthesize."
    exit 0
  fi
  : >"$log"
  if build; then
    say "ghmem: built its CLI environment in $venv" ""
  else
    say "ghmem: could not build its CLI environment in $venv (see $log); the ghmem command will not run until it is built." \
      "The ghmem command is unavailable in this session (its CLI environment could not be built). Do not dispatch ghmem:ghmem-triage or ghmem:ghmem-synthesize: they call ghmem and cannot work."
  fi
}

# A `ghmem` on PATH that is not this plugin's own launcher. The plugin's
# bin/ comes last on PATH, so that one shadows the launcher and a build here
# would go unused: a dogfood session in a direnv-activated checkout has the
# checkout's `.venv/bin/ghmem` first, and a uv tool install is the same case.
provided_elsewhere() {
  local found
  found="$(command -v ghmem 2>/dev/null)" || return 1
  [[ -n $found && ! $found -ef "$root/bin/ghmem" ]]
}

ready() {
  [[ -x "$venv/bin/ghmem" ]] && "$venv/bin/ghmem" --version >/dev/null 2>&1
}

# Take the lock, waiting up to four minutes for a build already under way.
acquire() {
  local waited=0
  find "$lock" -maxdepth 0 -mmin +10 -exec rmdir {} + 2>/dev/null
  until mkdir "$lock" 2>/dev/null; do
    ready && exit 0
    ((waited++ >= 240)) && return 0
    sleep 1
  done
  trap 'rmdir "$lock" 2>/dev/null' EXIT
}

build() {
  rm -rf "$venv"
  if command -v uv >/dev/null 2>&1 &&
    (cd "$root" && uv sync --frozen --no-dev --quiet) >>"$log" 2>&1 && ready; then
    touch "$marker"
    return 0
  fi
  echo "uv sync did not give a working environment; trying python3 -m venv" >>"$log"
  rm -rf "$venv"
  # The pinned requirements, then the tree itself without dependencies: the
  # same versions uv.lock gives, from nothing but the stdlib's venv and the pip
  # it bundles.
  python_ok || return 1
  python3 -m venv "$venv" >>"$log" 2>&1 &&
    "$venv/bin/pip" install --quiet -r "$root/requirements.txt" >>"$log" 2>&1 &&
    "$venv/bin/pip" install --quiet --no-deps "$root" >>"$log" 2>&1 && ready &&
    touch "$marker"
}

# ghmem needs python 3.14, which pip would refuse with a less direct message.
python_ok() {
  command -v python3 >/dev/null 2>&1 || {
    echo "no python3 on PATH" >>"$log"
    return 1
  }
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 14))' || {
    echo "python3 is $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'), and ghmem needs 3.14 or newer (uv fetches one; without uv there is none to use)" >>"$log"
    return 1
  }
}

# say <user text> <agent text>: the JSON on stdout. Built with printf because
# nothing else can be relied on to be there; the strings carry paths, so
# backslash and double quote are escaped, and a path with a newline is not
# handled.
say() {
  local user="${1//\\/\\\\}" agent="${2//\\/\\\\}"
  user="${user//\"/\\\"}"
  agent="${agent//\"/\\\"}"
  if [[ -z $agent ]]; then
    printf '{"systemMessage": "%s"}\n' "$user"
  else
    printf '{"systemMessage": "%s", "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$user" "$agent"
  fi
}

main "$@"
