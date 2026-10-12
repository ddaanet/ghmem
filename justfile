# Enable bash tracing (set -x) for all recipes. Usage: just trace=true <recipe>

trace := "false"

# List available recipes
help:
    @just --list --unsorted

# The commit gate, under the name the claude-plugin-dev toolkit expects
precommit: green

# The gate `release` depends on; the toolkit makes it mandatory.
prerelease: precommit

# Format code: ruff fixes, ruff format, docformatter
format:
    #!{{ bash_prolog }}
    sync
    require-docformatter
    run-format

# Format, lint, typecheck, test, check line limits
green:
    #!{{ bash_prolog }}
    sync
    require-docformatter
    # bats is a system tool, unpinned by uv.lock exactly like docformatter,
    # so its absence gets the same explained failure ahead of the cache
    # rather than a bare "command not found" from the middle of the run.
    if ! command -v bats > /dev/null; then
        echo "bats is not on PATH; the hook suites cannot run." >&2
        echo "Install it from your package manager: apt install bats" >&2
        exit 1
    fi
    # Skip the gate outright when nothing it depends on has changed
    # since it last passed. Every check is a pure function of the same
    # inputs, so a re-run over an untouched tree can only reprint the
    # verdict it printed last time — and `green` is cheap to invoke
    # twice while working through a change.
    if check-sentinel green; then
        echo "${GREEN}✓ Cached (inputs unchanged)${NORMAL}"
        exit 0
    fi
    run-format
    # Report every failing check, not just the first, and still exit
    # non-zero at the end. `report` clears this on failure and names the
    # check in `recap` on success.
    status=true
    recap=
    report "ruff check" ruff check -q
    report "docformatter -c" docformatter -c src tests
    report "mypy" mypy
    # pytest is the one check that is not quiet on success, and the only
    # one slow enough to be worth watching, so it tees its output live
    # rather than buffering it through `report`.
    set-tmpfile
    pytest_ok=true
    pytest 2>&1 | tee "$tmpfile" || pytest_ok=false
    # Match pytest's own summary wording, not the word anywhere in the
    # output: several tests here have "skipped" in a docstring, which a
    # failure would print back in the traceback.
    if grep -qE '[0-9]+ skipped' "$tmpfile"; then
        echo "${RED}Tests skipped — all tests must run${NORMAL}" >&2
        exit 1
    fi
    if $pytest_ok
    then
        passed=$(grep -oE '[0-9]+ passed' "$tmpfile" | tail -1) || passed=pytest
        recap-add "$passed"
    else status=false
    fi
    # The hook suites, which guard the agents' containment. bats prints a
    # line per case even when every one passes, so it is captured and shown
    # only on failure; its count joins the recap the way pytest's does.
    set-tmpfile
    if bats --formatter tap tests > "$tmpfile" 2>&1
    then recap-add "$(grep -c '^ok' "$tmpfile") bats"
    else
        status=false
        echo "${HEADER_STYLE}# bats${NORMAL}"
        cat "$tmpfile"
    fi
    report "line limits" ./scripts/check_line_limits.sh
    # Recap what ran, so a quiet gate reads as one whose checks all passed
    # rather than as one whose checks never executed. The sentinel records
    # the tree as `run-format` left it, not the one the run started with,
    # and only a passing run records anything.
    if $status; then
        gate-inputs-hash > "$sentinel"
        echo "${GREEN}✓ $recap${NORMAL}"
    else
        echo "${RED}✗ Red!${NORMAL}"
        exit 1
    fi

# Bash prolog
[private]
bash_prolog := "/usr/bin/env bash\n" + \
    ( if trace == "true" { "set -xeuo pipefail" } \
    else { "set -euo pipefail" } ) + "\n" + '''
run-format () {
    ruff check -q --fix-only
    ruff format -q
    docformatter src tests
}

# Run one check, printing a labelled section only if it had something to
# say. Every check here is quiet when it passes, and mypy is quiet even
# about succeeding (`error_summary = false`), so unlabelled output leaves
# no way to tell which check spoke — or whether one ran at all.
report () {
    header=$1; shift
    set-tmpfile
    if "$@" &> "$tmpfile"
    then recap-add "$header"
    else status=false
    fi
    if [ -s "$tmpfile" ]; then
        echo "${HEADER_STYLE}# $header${NORMAL}"
        cat "$tmpfile"
    fi
}

# Name a check that passed. A gate whose checks are all quiet has nothing
# to show for the work it did, so the verdict recaps what the silence
# stood for rather than asserting a bare "OK".
recap-add () { recap="${recap:+$recap · }$1"; }

HEADER_STYLE=$'\033[1;36m'  # Bold cyan
RED=$'\033[31m'
GREEN=$'\033[32m'
NORMAL=$'\033[0m'

# One scratch file per recipe run, for `report` to capture into. `tmp/`
# is gitignored, and unlike $TMPDIR it stays writable under the Claude
# Code sandbox.
set-tmpfile () {
    if [[ ! -v tmpfile ]]; then
        mkdir -p tmp
        tmpfile=$(mktemp tmp/justfile-XXXXXX)
        trap 'rm -f "$tmpfile"' EXIT
    fi
}

# Decide whether a gated recipe can skip its work, and say where it
# should record having passed. Sets `sentinel` and returns 0 when the
# hash recorded there still matches, so the caller can exit early;
# creates `tmp/` so the caller can write that file unconditionally.
check-sentinel () {
    mkdir -p tmp
    sentinel=tmp/.$1-sentinel
    [ -f "$sentinel" ] && [ "$(cat "$sentinel")" = "$(gate-inputs-hash)" ]
}

# Everything a gate outcome can depend on: the interpreter, the two
# tools the lockfile does not pin (docformatter and bats), the pinned
# dependencies, the tool configuration, the checks themselves, and the
# sources they run over — `hooks` among them, since the bats suites run
# over those scripts and a check whose inputs the hash does not cover
# would go stale-green on the next hook edit. `agents` and `.claude-plugin`
# join them for the same reason: test_plugin_layout runs over the agents'
# frontmatter and the manifest, and an agent frontmatter edit can break
# containment (a hook path that no longer resolves exits 127, which reads as
# "not a block"). Names are hashed alongside contents so a rename or a
# deletion counts as a change. `--others --exclude-standard` picks up files
# not yet added to git — a brand-new test file is exactly when a stale cached
# pass would hurt most — while still leaving the ignored caches out.
gate-inputs-hash () {
    {
        python3 --version
        docformatter --version
        bats --version
        git ls-files -z --cached --others --exclude-standard \
                justfile pyproject.toml uv.lock scripts src tests hooks \
                agents .claude-plugin \
            | sort -z \
            | while IFS= read -r -d '' file; do
                  printf '%s\n' "$file"
                  if [ -f "$file" ]; then cat "$file"; fi
              done
    } | cksum
}

# Tools (ruff, mypy, pytest) come from the direnv-activated venv on PATH.
# Do not uv sync when in the Claude Code sandbox, whose uv cache is
# read-only.
sync () { if [ -w /tmp ]; then uv sync -q; fi; }

# docformatter is the exception: it has no Python 3.14 support (an
# incompatible transitive dependency), so it cannot be a dev dependency
# and lives outside the project venv. Without this guard its absence
# surfaces as a bare "command not found" from whichever recipe reached
# it first, which says nothing about why it is missing or how to fix it.
require-docformatter () {
    command -v docformatter >/dev/null && return
    echo "docformatter is not on PATH." >&2
    echo "It cannot be a dev dependency: no Python 3.14 support." >&2
    echo "Install it standalone: uv tool install docformatter" >&2
    return 1
}
'''
