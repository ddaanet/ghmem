# Item 1.1, slice 1 — code review (implementation)

Diff under review: commit `921494b`, restricted to
`tools/ghmem/src/ghmem/routing_reduce.py` and
`tools/ghmem/src/ghmem/ledger.py`. Tests read but not touched.

**Verdict: pass, with one fix applied** — an inaccurate autoflush comment in
`_release_topic`, found by tracing the emitted SQL rather than by reading.
Everything else the runbook prescribes is present and behaves as claimed;
both load-bearing orderings are discriminated by the slice's tests.

## 1. `_apply_route_event` ordering — pass

`src/ghmem/routing_reduce.py:123-141`:

- `left = previous.verdict if previous is not None else None` sits **before**
  `session.merge`, and binds the **string**, not the ORM instance. Nothing
  dereferences `previous` after the merge.
- `_release_topic(session, left)` is the last statement of the function,
  after the merge and after the join-side `_mark_topic_dirty`. The
  non-load-bearing position matches the runbook's fixed convention (release
  after the join-side dirty), so there is no convention deviation to report.
- Guard is `if left is not None and left != NO_SIGNAL:` — exactly "neither
  `None` nor `NO_SIGNAL`".
- Summary line is verbatim `Replace an item's verdict, releasing the topic it
  leaves.` The physical line measures 67 characters including the indent and
  both triple-quotes, i.e. 64 up to and including the closing period as the
  runbook counts it — clear of the 81–88 docformatter dead zone.

## 2. `_mark_topic_dirty` as a conditional upsert — pass

`src/ghmem/routing_reduce.py:280-301`. Built with
`from sqlalchemy.dialects.sqlite import insert as sqlite_insert` (`:20`),
character-for-character the import at `dispatch_store.py:20`; keyed
`index_elements=["name"]`, `set_={"dirty": True}`; issued as
`session.execute(stmt)`. Summary line unchanged.

The `session.execute()` autoflush is not taken on trust. Isolated probe — a
pending ORM `DELETE` of `Topic("softwrap")`, then `_mark_topic_dirty` for the
same name, with a `before_cursor_execute` listener:

```
   SQL: DELETE FROM topic WHERE topic.name = ? ('softwrap',)
   SQL: INSERT INTO topic (name, dirty) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET dirty = ? ('softwrap', 1, 1)
final: [Topic(name='softwrap', dirty=True)]
```

The DELETE is emitted by the upsert's own autoflush, ahead of the INSERT.
The end-to-end replay case behaves accordingly: replaying
`I_1→softwrap, I_2→wrapping, I_1→wrapping, I_2→softwrap` in one `Session`
leaves `Topic(name='softwrap', dirty=True)` and
`Topic(name='wrapping', dirty=True)`.

**Observation for slice 4, not a defect here.** In the *reducer's* own call
path the pending `Topic` DELETE is already flushed before the upsert is
reached — by `session.merge`'s autoflush at the top of the next
`_apply_route_event`. Traced over the four-event replay above:

```
== route I_2->softwrap (re-mints)
   SQL: DELETE FROM topic WHERE topic.name = ? ('softwrap',)
   SQL: UPDATE route SET verdict=? WHERE route.item_id = ? ('softwrap', 'I_2')
   SQL: INSERT INTO topic (name, dirty) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET dirty = ? ('softwrap', 1, 1)
```

So slice 4's prescribed mutation — issue the upsert on the connection instead
— may well stay green for the same reason the recall checkpoint found the old
get-then-add stayed green: an earlier autoflush intervenes. That is slice 4's
discrimination claim to test, and the runbook already requires it be run
against its mutation. Flagged so it is not discovered as a surprise.

## 3. `_release_topic` docstring — pass, after one fix

- The `Scoped to "unroute"` paragraph is gone in full, open-question sentence
  included, replaced by "Withdrawal and re-route both empty a topic this way,
  and both release it here."
- The `docs/design/<name>.md` paragraph keeps the replay-destructiveness
  argument and the `ghmem index` clause; "a synthesized doc also remains true
  prose about items that really were routed there" is gone.
- The `Snapshot` paragraph is byte-identical to before.
- Both pending-write shapes and the concurrency argument are present in the
  comment.

**Fix applied (minor, accuracy).** As committed the comment read "The caller's
pending write … *is still unflushed here*. SQLAlchemy's autoflush emits it
before this query", and "That autoflush is also where SQLite's writer lock is
taken". On the dominant `route` path that is false: the join-side
`_mark_topic_dirty`'s `session.execute()` runs first and its autoflush has
already emitted the `UPDATE route`. Traced on a re-route into a live topic:

```
== re-route I_1 -> wrapping (real topic)
   SQL: UPDATE route SET verdict=? WHERE route.item_id = ? ('wrapping', 'I_1')
   SQL: INSERT INTO topic (name, dirty) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET dirty = ? ('wrapping', 1, 1)
   SQL: SELECT count(*) AS count_1 FROM route WHERE route.verdict = ? ('softwrap',)
   SQL: DELETE FROM topic WHERE topic.name = ? ('softwrap',)
```

The `UPDATE` precedes the `COUNT` either way, so the code is correct and no
behaviour changed; only the stated mechanism was wrong, and wrong in the
direction that matters — a comment justifying an ordering constraint by a
flush that does not happen where it says. Rewritten to name both flush sites
("at the join-side `_mark_topic_dirty`'s `session.execute` where a re-route
has one, and at this query otherwise") and to state the lock as held *from
that write onwards* rather than taken at one specific autoflush, which is
also true under replay, where the transaction's first write is much earlier.
The conclusion — `COUNT` and `DELETE` on the same side of the lock, sibling
either counted or re-minted — is unchanged.

## 4. `ledger.py`'s `NO_SIGNAL` docstring — pass

Exactly one sentence added, carrying both halves of the required content:
"Routing an item *to* it still releases the topic the item leaves, dropping
that row if the item was its last." The pre-existing claim ("marks an item as
triaged without creating or dirtying any `Topic` row") is untouched and still
true. `git show 921494b -- tools/ghmem/src/ghmem/ledger.py` is that hunk and
nothing else.

## 5. The self-declared widening, and the sweep — pass

`apply_routing_event`'s `"route"` paragraph (`:57-62`) is correct as
rewritten: the join-side dirty is conditional on the new verdict, the
leave-side release is conditional on a real prior verdict, and "the same
release `"unroute"` performs below" points at a paragraph that is indeed
below it in the same docstring. It states the surviving rule positively and
gives no account of what the text used to say. The closing "drops that row
rather than leaving it dirty" is a contrast with the *sibling* operation
named in the same sentence — the join-side dirtying a reader would otherwise
expect symmetrically — not with a former draft, so it stays.

Sweep of both files, and of the rest of `src/ghmem/` for anything this change
falsifies: nothing else stands.

- `_apply_unroute_event`'s "skipped for `NO_SIGNAL` for the same reason the
  `"route"` and `"reenter"` branches skip it" — still true of both.
- `_release_topic`'s "A `Topic` row exists exactly while some `Route` carries
  its name" — now unconditionally true rather than true only under `unroute`.
- `exceptions.py:147-150` and `triage.py:307-310` (a live re-route is refused
  by `record_verdict`) — untouched by this change.
- `TopicNotRoutedError`, `status.py`'s `build_status`, `models.py`'s `Topic`
  — carry no lifecycle claim at all.
- `_mark_topic_dirty`'s "A conditional upsert rather than a get-then-add" is
  a rejected design alternative, not draft archaeology; it stays under
  `memory/ddaanet/no-doc-history-references.md`'s own carve-out.

## 6. Mutation check — both mutations red, as claimed

Method: `cp` of `src/ghmem/routing_reduce.py` saved outside the tree, each
mutation applied **in place**, `pytest` run from `tools/ghmem` against the
tests where they live, file restored from the copy between runs.

### Mutation A — `_release_topic` moved before `session.merge`

```
=========================== short test summary info ============================
FAILED tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild - As...
FAILED tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row
FAILED tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute
FAILED tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic
FAILED tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic
FAILED tests/test_routing_replay.py::test_rebuild_is_idempotent - AssertionEr...
6 failed, 11 passed in 1.20s
```

Representative failure, on the assertion rather than an error:

```
tests/test_routing_live.py:231: in test_route_to_no_signal_drops_the_emptied_topic
    assert session.get(Topic, "softwrap") is None
E   AssertionError: assert Topic(name='softwrap', dirty=True) is None
```

### Mutation B — `left` read after the merge

```
=========================== short test summary info ============================
FAILED tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild - As...
FAILED tests/test_routing_live.py::test_reroute_of_the_last_item_drops_the_topic_row
FAILED tests/test_routing_live.py::test_status_dirty_set_stays_assemblable_after_a_reroute
FAILED tests/test_routing_live.py::test_route_to_no_signal_drops_the_emptied_topic
FAILED tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_drops_the_emptied_topic
FAILED tests/test_routing_replay.py::test_rebuild_is_idempotent - AssertionEr...
6 failed, 11 passed in 7.30s
```

Both mutations red the same six tests — the whole red batch except
`test_route_creates_topic_marked_dirty`, which asserts on `"wrapping"` and is
insensitive to either ordering by construction (the runbook says so). Under
mutation B the surviving `Route` still carries the *new* verdict when the
count runs, so `left` names the joined topic and the leaving one is never
released; under mutation A the count still sees the un-updated route. The
tests discriminate both, so the runbook's two load-bearing orderings are
evidenced, not asserted.

Restoration confirmed: after the second restore,
`git diff -- tools/ghmem/src/` printed nothing and `git status --short` was
empty. The working tree's only modification now is this review's own fix to
`routing_reduce.py`, uncommitted as required.

## 7. Line-count cap

`tools/ghmem/src/ghmem/routing_reduce.py` — **353** lines after the fix (351
as committed, +2 from the reworded comment). 47 lines clear of the 400-line
cap for slices 2–4 and Phase 2.

## Fixes applied

1. `_release_topic`'s autoflush comment
   (`tools/ghmem/src/ghmem/routing_reduce.py:259-270`) — rewritten so the
   flush is attributed to the site it actually happens at on each path, and
   the writer lock is described as held from that write onwards. Behaviour
   unchanged; +2 lines.

Nothing found that was unfixable in scope, and nothing that could only be
defended by editing a test.

## Gate

`just precommit` from the repository root, after the fix:

```
✓ ruff check · docformatter -c · mypy · 280 passed · 47 bats · line limits
```
