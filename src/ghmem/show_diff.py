"""The `## Diff` section of a rendered PR, and the cap on its body.

`show.py` assembles markdown from what a thread says. This module is
the one part of that render governed by arithmetic instead: a PR diff
is the only unbounded thing in a packet, so it is the only section with
a character cap, a cut that has to be a real ceiling, and a caller
outside `show` — `triage`'s fit path — that narrows the cap to whatever
a batch has left.

`render_diff` emits the section. `diff_body_dropped` answers the
question a rendered section cannot: how much a given cap left out.
"""

from ghmem.aliases import IssueOrPRNumber
from ghmem.exceptions import PRDiffNotFoundError
from ghmem.models import FileStat
from ghmem.store import Store

DIFF_BODY_MAX_CHARS = 25_000  # PROVISIONAL — calibrated in subproject 4
"""A safety valve on one rendered diff body, not a display budget.

The median PR in this corpus touches 16 lines and only 10 of 1,299 exceed 2,000,
so at this size an ordinary diff renders whole and only the vendor-dump tail is
cut — a tail whose body carries no decision signal beyond its file list. Roughly
6,000 tokens, about an eighth of one triage batch.
"""

__all__ = ["DIFF_BODY_MAX_CHARS", "diff_body_dropped", "render_diff"]


def render_diff(
    store: Store, number: IssueOrPRNumber, diff_max_chars: int
) -> list[str]:
    """Render the diffstat and (possibly cut) diff body for a PR.

    Issues have no diff — `PRDiffNotFoundError` means the section is
    omitted entirely. The diffstat file list always renders in full,
    whatever the cap: it is the part of a diff that keeps its signal
    when the body is gone. The truncation marker follows the closing
    fence when the cut fired here, or when the fetched diff was already
    `truncated`.

    The marker sits **outside** the fence because it is not diff. A
    unified diff has no line that starts with `…`, so inside the fence
    it is a line the reader has to recognise as not being part of the
    patch — and a `diff`-highlighting renderer marks it up as one
    anyway. Below the fence it reads as what it is: a note about the
    block above it.

    The (possibly empty) body is appended to `lines` unconditionally,
    even when it is `""`, rather than only `if body:`. That keeps the
    rendered packet's length exactly `floor + len(body)` for every cap,
    where `floor` is this same render at `diff_max_chars=0` — so a
    caller can size a packet against a budget by measuring the floor
    once and growing it by precisely the cut body's length.
    """
    try:
        diff = store.pr_diff_for(number)
    except PRDiffNotFoundError:
        return []
    lines = ["## Diff", "", *[_format_file_stat(f) for f in diff.diffstat], ""]
    body, cut = _cut_diff_body(diff.diff_text, diff_max_chars)
    lines.append("```diff")
    lines.append(body)
    lines.append("```")
    if cut or diff.truncated:
        lines.append("… [diff truncated] …")
    lines.append("")
    return lines


def _format_file_stat(f: FileStat) -> str:
    """Render one diffstat line: `path +additions -deletions`."""
    return f"- {f.path} +{f.additions} -{f.deletions}"


def diff_body_dropped(
    store: Store, number: IssueOrPRNumber, diff_max_chars: int
) -> int:
    """Return the diff-body characters `diff_max_chars` drops for `number`.

    Zero when the body fits the cap whole, and zero for an item that
    has no diff at all. The cut is the very one `render_diff` performs,
    so the two can never disagree about where the ceiling falls.

    A rendered packet reports only the text that survived, which
    cannot answer how much was left out — and how much a given cap
    drops is exactly what calibrating that cap needs. `triage` records
    this against `DIFF_BODY_MAX_CHARS` for every item it dispatches,
    so the valve can be sized from the diffs actually served rather
    than from the corpus-wide length distribution.
    """
    try:
        diff = store.pr_diff_for(number)
    except PRDiffNotFoundError:
        return 0
    body, _cut = _cut_diff_body(diff.diff_text, diff_max_chars)
    return len(diff.diff_text) - len(body)


def _cut_diff_body(diff_text: str, diff_max_chars: int) -> tuple[str, bool]:
    """Cut `diff_text` to at most `diff_max_chars`, on a whole-line boundary.

    Returns the cut text and whether a cut happened. `diff_max_chars`
    is a real ceiling: the returned text never exceeds it. The cut
    lands at the last newline at or before the cap, not the first one
    past it, so a caller sizing a packet against a budget can trust
    the bound. A cap of zero, and a first line longer than the cap,
    both keep nothing — the diffstat above still renders regardless.
    """
    if len(diff_text) <= diff_max_chars:
        return diff_text, False
    if diff_max_chars <= 0:
        return "", True
    cut = diff_text.rfind("\n", 0, diff_max_chars + 1)
    return (diff_text[:cut] if cut != -1 else ""), True
