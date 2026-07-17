#!/usr/bin/env bash
# Check that source files do not exceed 400 lines.
#
# Pilfered from the sibling `edify` repo, which wires the same 400-line
# cap into its precommit gate.

set -euo pipefail

MAX_LINES=400
EXIT_CODE=0

while IFS= read -r -d '' file; do
    line_count=$(wc -l < "$file")
    if [ "$line_count" -gt "$MAX_LINES" ]; then
        printf '%s: %s lines (exceeds %s line limit)\n' \
            "$file" "$line_count" "$MAX_LINES" >&2
        EXIT_CODE=1
    fi
done < <(find src tests -type f -name "*.py" -print0 2>/dev/null)

exit "$EXIT_CODE"
