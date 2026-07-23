# ghmem dispatch B2 — solo-item fit

Date: 2026-07-22
Status: approved (design), pending implementation plan

Scope: subproject 2 (dispatch), Part B2. Depends on B1 (landed).
Part C (`topic` / `close` / `index`) is unaffected.

## Problem

`triage.serve_next` exempts a session's first item from the character
gate, so no single oversized item can deadlock the queue. B1 serves
that item whole, whatever its size. B2 is supposed to make it fit.

The mechanism the design called for cannot do that.

**The diff body is already tiny.** `show._render_diff` caps every diff
body at `DIFF_BODY_MAX_CHARS`, currently 300 characters, on every
render. Cutting 300 characters cannot bring a packet under a 200,000
character budget. The constant was chosen as a display cap that keeps
test fixtures readable; it behaves as a hard ceiling on the most
informative part of a PR packet, which is the opposite of its intent.

**Only one of three size contributors can be cut.** A packet is thread
text, then the diffstat file list, then the diff body. Thread text is
never cut. The file list always survives — a vendor-dump PR touching
several thousand files runs to tens of thousands of characters on the
file list alone. The diff body is the only cuttable part, so "truncate
the diff to fit" is not achievable as a guarantee: a large enough
thread or file list exceeds the budget with the diff body already at
zero.

The progress guarantee the exemption actually delivers is that the
queue never deadlocks. It was written as though it also guaranteed
that every packet fits. It never could.

## Approach

Cut what can be cut, then be honest about the rest.

The first item's diff body is truncated against whatever the batch
budget has left after the uncuttable parts. When that is not enough,
the item is served oversized anyway — the queue must advance — and
both the packet and the ledger say so. Recording the outcome is what
lets subproject 4 tell three states apart when it calibrates
`BATCH_CHARS`: the packet fit, the packet fit only after the diff was
cut, or the packet could not fit at all.

The diff cap is restated as what it should have been from the start: a
safety valve against the vendor-dump tail, not a ceiling on ordinary
diffs.

## Design

### The safety valve

`DIFF_BODY_MAX_CHARS` becomes `25_000`, still PROVISIONAL and still
calibrated in subproject 4.

The measured shape of the corpus justifies the size: the median PR
touches 16 lines, p99 is about 1,600, and only 10 of 1,299 PRs exceed
2,000 changed lines. At 25,000 characters an ordinary PR diff renders
whole and only that tail is cut — which is the outcome the corpus
design already argued for, since a 228,000-line Go-modules dump
carries no decision signal beyond its file list.

25,000 characters is roughly 6,000 tokens, about an eighth of one
batch. It is a valve, not a budget.

### `show.render`

`render` gains a keyword-only `diff_max_chars: int` defaulting to
`DIFF_BODY_MAX_CHARS`, forwarded to `_render_diff`. `ghmem show`
passes nothing and is unchanged.

Two rules inside `_render_diff` change:

**The cut never overshoots.** Today the body is cut at the first
newline *past* the cap, so the rendered body can exceed it by the
length of one line. That is harmless for a display cap and wrong for a
fit computation, which needs the cap to be a real ceiling. The body is
now cut at the last newline at or before the cap. A single line longer
than the cap therefore renders as no body at all, followed by the
marker.

**A cap of zero means no body.** `diff_max_chars=0` emits an empty
fence followed by the `… [diff truncated] …` marker. The diffstat file
list still renders in full. This is the floor render the fit
computation measures against.

### The fit path in `serve_next`

The common path is unchanged: one render at the valve, gate, reserve.
The fit path runs only when the first item of a session produces a
packet larger than `BATCH_CHARS`.

1. Re-render the item with `diff_max_chars=0` and measure it as
   `floor_chars`. The floor already includes the fence and the
   truncation marker, so everything left over is body space.
2. `allowance = BATCH_CHARS - floor_chars`.
3. When `allowance > 0`, re-render at `diff_max_chars=allowance`. The
   result fits by construction, because the cut is now a real ceiling.
   Outcome: `diff_cut`.

   The allowance is body space exactly, with nothing held back for
   the newline joining the body to the marker: the render emits the
   body element even when empty, so that separator is already counted
   in the floor. A packet is the floor plus the length of the body it
   keeps, exactly, at every cap. The marker itself is never at risk of
   being dropped: reaching this path at all means the valve-truncated
   body already overran the budget, so the allowance is always smaller
   than the body and the cut always fires.
4. When `allowance <= 0`, serve the floor packet. Thread text and file
   list alone exceed the budget and neither may be cut. Outcome:
   `oversized`.

A non-first item never enters this path. It is gated normally, and a
packet that fails the gate is discarded with no `dispatched` event, so
it stays queued.

Ordering note: the fit path runs before `reserve_dispatch`, like the
gate it replaces for this item, so a reservation lost to a concurrent
session costs at most the extra renders and never a wasted triage.

### The packet note

A packet whose outcome is not `fit` carries one extra header line,
beside `packet-chars`:

```
packet-note: diff truncated to fit the batch budget
packet-note: served without its diff body; thread text and file list leave no budget for it
```

`packet_chars` continues to measure the item line and the rendered
thread only, excluding the header block, so adding this line does not
change what the budget counts.

### The ledger record

`DispatchedData` gains a required field:

```python
fit: Literal["fit", "diff_cut", "oversized"]
```

Required, with no default and no tolerance for its absence: an event
missing it is a malformed event, not an old one. This is internal
pre-release tooling and there is no back-compatibility to keep.

One tag rather than a pair of booleans. The three states are mutually
exclusive and a pair would admit a fourth combination that cannot
occur.

**No `Dispatch` column.** The field is written to the ledger and never
read back by the reducer. `RequeueData.session` already establishes
this shape — a write-only signal that subproject 4 reads off the
ledger to calibrate batch budgets. `ingest.apply_routing_event` reads
named keys, so the new one passes through untouched and the live
tables still converge byte-for-byte with a cold rebuild.

## Testing

**Large fixtures are generated, never versioned.** A new uncollected
`tests/diff_fixtures.py` builds oversized diff text at runtime,
following the `triage_helpers.py` and `sync_helpers.py` pattern. The
generator is what gets reviewed and versioned; a 25,000-character blob
in `show_corpus.jsonl` would be neither readable nor reviewable.

Changes to existing tests:

- `test_diff_body_max_chars_is_modest` asserted `< 1000` and dies at
  25,000. Its property was "small enough to keep fixtures readable",
  which stops being true or desirable. It is replaced by a bound that
  states the valve's actual intent: large enough that an ordinary PR
  diff renders whole, small enough to stay a fraction of one batch.
- `test_large_diff_body_truncated_with_marker` drives the generator
  rather than the 645-character fixture diff, which no longer reaches
  the cap. Its assertion about the last rendered line changes with the
  cut rule: the last whole line at or before the cap, not the first
  one past it.

New coverage:

- Each of the three `fit` tags reaches the ledger.
- The `allowance <= 0` boundary: an item whose thread and file list
  alone exceed `BATCH_CHARS` is served oversized, with the file list
  intact and the diff body empty.
- A `diff_cut` packet is genuinely at or under `BATCH_CHARS` — the
  claim "fits by construction" is asserted, not assumed.
- A non-first item never enters the fit path, even when oversized.
- Thread text is never cut on any path.

Mutation targets, to prove the tests have teeth: revert the cut rule
to the overshooting `find`, and a `diff_cut` packet should exceed the
budget. Drop the `fit` field from the event, and the ledger assertions
should die. Remove the `allowance <= 0` branch, and the oversized case
should fail rather than silently serving a negative cap.

## Out of scope

- Calibrating `DIFF_BODY_MAX_CHARS`, `BATCH_CHARS` or `BATCH_ITEMS`
  against the live corpus. All three stay PROVISIONAL for subproject 4.
- Cutting thread text under any circumstance.
- Cutting or eliding the diffstat file list, including for the
  vendor-dump tail where it is the whole of the packet's size.
- Any stderr signal for an oversized serve. The packet is stdout and
  carries the note; stderr stays reserved for the terminal messages,
  which an oversized serve is not.
- A re-route path, and anything in Part C.
