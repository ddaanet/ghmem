# Enable bash tracing (set -x) for all recipes. Usage: just trace=true <recipe>

trace := "false"

# List available recipes
help:
    @just --list --unsorted

# Format code: ruff fixes, ruff format, docformatter
format:
    #!{{ bash_prolog }}
    sync
    require-docformatter
    ruff check -q --fix-only
    ruff format -q
    docformatter src tests

# Format, lint, typecheck, test, check line limits
green: format
    #!{{ bash_prolog }}
    sync
    safe ruff check -q
    safe docformatter -c src tests
    safe mypy
    safe pytest
    safe ./scripts/check_line_limits.sh
    end-safe

# Bash prolog
[private]
bash_prolog := "/usr/bin/env bash\n" + \
    ( if trace == "true" { "set -xeuo pipefail" } \
    else { "set -euo pipefail" } ) + "\n" + '''
# Report every failing check, not just the first, and still exit non-zero.
safe () { "$@" || status=false; }
end-safe () { ${status:-true}; }

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
