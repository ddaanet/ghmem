# Enable bash tracing (set -x) for all recipes. Usage: just trace=true <recipe>

trace := "false"

# List available recipes
help:
    @just --list --unsorted

# Format code: ruff fixes, ruff format, docformatter
format:
    #!{{ bash_prolog }}
    sync
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

# Tools (ruff, mypy, pytest, docformatter) come from the direnv-activated
# venv on PATH. Do not uv sync when in the Claude Code sandbox, whose uv
# cache is read-only.
sync () { if [ -w /tmp ]; then uv sync -q; fi; }
'''
