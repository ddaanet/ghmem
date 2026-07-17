#!/usr/bin/env bash
# Check that source files do not exceed 400 lines.
#
# Pilfered from the sibling `edify` repo, which wires the same 400-line
# cap into its precommit gate, plus the PENDING_SPLIT escape hatch below.

set -euo pipefail

MAX_LINES=400
EXIT_CODE=0

# TEMPORARY: files known to exceed the cap, split scheduled. This list
# is meant to shrink to empty and then be deleted along with the code
# that reads it — never add to it to make a check pass. Each entry is
# reported on every run so it cannot rot quietly.
PENDING_SPLIT=(
    src/ghmem/ingest.py
    src/ghmem/sync.py
    tests/test_sync.py
)

is_pending() {
    local candidate=$1 pending
    for pending in "${PENDING_SPLIT[@]}"; do
        [ "$candidate" = "$pending" ] && return 0
    done
    return 1
}

while IFS= read -r -d '' file; do
    if is_pending "$file"; then
        continue
    fi
    line_count=$(wc -l < "$file")
    if [ "$line_count" -gt "$MAX_LINES" ]; then
        printf '%s: %s lines (exceeds %s line limit)\n' \
            "$file" "$line_count" "$MAX_LINES" >&2
        EXIT_CODE=1
    fi
done < <(find src tests -type f -name "*.py" -print0 2>/dev/null)

for pending in "${PENDING_SPLIT[@]}"; do
    if [ ! -e "$pending" ]; then
        printf 'pending-split file no longer exists, drop it from the list: %s\n' \
            "$pending" >&2
        EXIT_CODE=1
        continue
    fi
    line_count=$(wc -l < "$pending")
    if [ "$line_count" -gt "$MAX_LINES" ]; then
        printf 'exempt, split pending: %s (%s lines)\n' "$pending" "$line_count"
    else
        printf 'now under the cap, drop it from the list: %s (%s lines)\n' \
            "$pending" "$line_count" >&2
        EXIT_CODE=1
    fi
done

exit "$EXIT_CODE"
