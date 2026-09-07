# Runbook — `route` deletes a topic it empties

Design: `plans/reroute-empty-topic/outline.md`. Recall:
`plans/reroute-empty-topic/recall-artifact.md`. Requirements are
`docs/ghmem-design.md`'s FR4, FR6, FR7, NFR5 and NFR8 — this job authors none
of its own. All paths below are relative to the repository root; the Python
package lives under `tools/ghmem/`.

## Questions the outline left open, settled here

**Where the new `route` tests live.** Add `_dispatch(item_id)` and
`_route(item_id, verdict, updated_at)` builders to
`tools/ghmem/tests/test_routing_live.py`, mirroring `test_unroute.py:20-45`.
The new tests are `route` tests and belong in the live file; what it lacks is
only a multi-item builder.

Extracting a shared `tools/ghmem/tests/routing_helpers.py` was considered and
rejected *for this change*. It is the repo's own convention (`sync_helpers`,
`triage_helpers`, `topic_helpers`) and five routing test files already
duplicate `_live_store`/`_session` over the same `routing_fetch.jsonl` — but
three of those five (`test_reenter.py`, `test_requeue.py`, and `test_unroute.py`
itself, the builders' source) sit outside this job's boundaries, and an
extraction migrating only the files this change touches leaves the duplication
standing while adding an import layer. **Follow-up, not built here: extract
`tools/ghmem/tests/routing_helpers.py` across all five routing test files in one
pass.**

The existing module constants `DISPATCH_1`, `ROUTE_SOFTWRAP` and
`ROUTE_WRAPPING` stay. Three existing tests read them and replacing them is
churn this change does not need.

**Whether FR7 gains a topic-lifecycle clause.** Yes — one clause, and only in
FR7. As written FR7 says what each correction mechanism does to the *verdict*
and nothing about the `Topic` row, and its Rationale pointer led to a decision
scoped to `unroute` alone; after this change all three corrections are uniform,
so retitling the pointer without touching the prose would leave FR7 describing
a distinction that no longer exists. FR4 and FR6 are not touched: FR6's subject
is incrementality and FR4's is the doc form, and restating the invariant in
either duplicates it. Details in Item 2.3.

## Correction to the outline, from a probe run at the recall checkpoint

The outline calls the within-one-replay-session emptied-then-re-minted case
"the only deterministic test of the upsert". It is not a test of the upsert:
probed directly, that scenario stays **green under the unchanged get-then-add
`_mark_topic_dirty`**, because `session.get` autoflushes the pending `DELETE`
before the recreate. Slice 5 below is named for what it actually discriminates.

The outline's red set reproduced exactly — 5 failed, 273 passed.

## Requirements mapping

| Requirement | Phase | Items | Notes |
|---|---|---|---|
| FR4 — synthesis writes one living document per topic | 1 | 1.1 | The empty dirty row `status` offers and `topic.assemble` refuses with `TopicNotRoutedError` is the breakage removed |
| FR6 — the pipeline is incremental | 1 | 1.1 | Every name in the dirty set stays assemblable |
| FR7 — a verdict is correctable | 1, 2 | 1.1, 2.2, 2.3, 2.4, 2.5 | Re-route joins `unroute` in releasing an emptied topic |
| NFR5 — the routing ledger is the system's own memory | 1, 2 | 1.1, 2.2, 2.5, 2.6 | The upsert stops a losing verdict dying before its ledger append; 2.6 guards the one corpus file a re-sync cannot regenerate |
| NFR8 — the toolchain gate is the bar for every change | 2 | 2.1 | |

## Phase 1: the reducer converges on `unroute` (type: tdd)

- Item 1.1: `tools/ghmem/src/ghmem/routing_reduce.py` — `_apply_route_event`
  releases the topic it leaves, and `_mark_topic_dirty` becomes a conditional
  upsert. Requirements: FR4, FR6, FR7, NFR5.
  Model: sonnet

  Three edits to the module:

  - `_apply_route_event` — capture the pre-merge verdict as `left` before
    `session.merge`, then call `_release_topic(session, left)` **after** the
    merge and after the join-side `_mark_topic_dirty`, when `left` is neither
    `None` nor `NO_SIGNAL`. Both orderings are load-bearing. The release must
    follow the merge because `_release_topic` counts the routes still carrying
    the name and relies on autoflush emitting the pending `UPDATE` first —
    called before it, the item still carries the old verdict, the count is
    never zero, and the row is never dropped. `left` must be read before the
    merge because `session.get(Route, id)` returns the identity-mapped instance
    `merge` then updates in place, so reading `previous.verdict` afterwards
    yields the *new* verdict. The summary line becomes `Replace an item's
    verdict, releasing the topic it leaves.` — 62 characters with indent, inside
    the 80-character ceiling docformatter's 81–88 dead zone imposes. It is the
    only summary line that changes.
  - `_release_topic` — delete the paragraph beginning "Scoped to `"unroute"`"
    in full, including its open-question sentence, and state the rule as
    covering withdrawal and re-route alike. Reword the autoflush comment: the
    caller's pending write is a `delete` under `unroute` and an `update` under
    `route`. Add the concurrency argument, which is the stronger reason the
    ordering matters: the autoflush emitting that pending write is where
    SQLite's writer lock is taken, so the `COUNT` and the `DELETE` land on the
    same side of it, and a worker routing another item into the same topic
    cannot commit between them — whichever order the two reach the lock in, the
    count sees the sibling and dirties, or the topic is dropped and the
    sibling's route re-mints it. Soften the `docs/design/<name>.md` paragraph:
    keep the replay-destructiveness argument, which is load-bearing, and drop
    the clause "a synthesized doc also remains true prose about items that
    really were routed there" — under re-route the item carries a *live* verdict
    naming a different topic, so the doc is contradicted by current state rather
    than merely unaccompanied by it. The `Snapshot` paragraph survives unchanged;
    its argument carries over to `route` as written.
  - `_mark_topic_dirty` — becomes a conditional upsert on `Topic`, keyed on
    `name`, setting `dirty` true on conflict, issued through the reducer's own
    `session.execute()` and built with `sqlalchemy.dialects.sqlite.insert`
    (imported as `sqlite_insert`, matching `dispatch_store.py:20`). Its
    docstring states why it is an upsert and not a get-or-create by pointing at
    `reserve_dispatch`'s reasoning rather than restating it, and records that
    `session.execute()`'s autoflush is load-bearing: `rebuild_from_ledgers`
    replays the whole routing ledger in one `Session`, so a topic emptied at one
    event and re-minted at a later one is a within-session delete-then-recreate,
    and the pending `DELETE` has to flush before the recreate or it wipes the new
    row.

  Widened deliberately: `sqlite_insert` puts a dialect-specific import in a
  module that is currently dialect-neutral. Consistent with `dispatch_store.py`,
  and the mirror is SQLite-only.

  Interfaces:
  - `_apply_route_event(session: Session, data: RouteData) -> None` — signature
    unchanged; post-condition gains "the topic named by the pre-merge verdict is
    released".
  - `_release_topic(session: Session, name: str) -> None` — signature unchanged;
    now reached from the `route` branch as well as `unroute`.
  - `_mark_topic_dirty(session: Session, name: str) -> None` — signature
    unchanged; now a conditional upsert executed through `session.execute()`.
  - Shared prose contract, stated once in `_release_topic`'s docstring and
    restated in `docs/ghmem-decisions.md` (Item 2.2): nothing removes
    `docs/design/<name>.md`, because this reducer is replayed wholesale by
    `rebuild_from_ledgers` and a filesystem delete inside it would make a rebuild
    destructive.

  Slices:

  1. **The emptied topic is dropped.** Reds against unchanged code — five
     assertions, probe-confirmed.
     - `tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_dirties_both_topics`
       → rename to `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic`.
       Assert `session.get(Topic, "softwrap") is None`;
       `session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)`;
       and the `Route` row equal in full to the fixture's second event —
       verdict `"wrapping"`, note `"actually about wrapping"`, session `"s2"`,
       `item_updated_at` `2026-01-01T00:00:00+00:00`. Delete the docstring
       paragraph beginning "The *old*-topic half of the reducer is NOT proven
       here" **in full** rather than rewording it: it documents a limitation this
       change removes, and a rebuild can now distinguish the two.
     - `tests/test_routing_live.py::test_reroute_dirties_the_topic_it_leaves`
       → replaced by `test_reroute_of_the_last_item_drops_the_topic_row`. Route
       `I_1` to `"softwrap"`, then re-route `I_1` to `"wrapping"`, both through
       `store.record_routing_event`; assert
       `session.get(Topic, "softwrap") is None` and
       `session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)`.
       Drop the `dirty = False` cleaning step — it existed to defeat the
       born-dirty trap for a `dirty is True` assertion and guards nothing against
       `is None`. Delete the docstring's second paragraph, whose claim that
       "dropping the reducer's old-topic dirtying changes nothing observable"
       this change falsifies.
     - `tests/test_routing_replay.py::test_route_creates_topic_marked_dirty` —
       retarget the topic assertion to `"wrapping"` and rewrite the docstring,
       which names "softwrap" in both its `When:` and its `Then:`. This is
       fixture coupling, not a contract change: the shared ledger re-routes item
       #1 away from `"softwrap"` at its last event. The second assertion,
       `route.item_updated_at.isoformat() == "2026-01-01T00:00:00+00:00"`,
       survives untouched — the fixture's re-route carries the same stamp as the
       original route.
     - `tests/test_routing_replay.py::test_rebuild_is_idempotent` — expected set
       becomes `{"wrapping"}`, **and add** `assert session.get(Topic, "softwrap")
       is None`. The hardcoded set is this test's only non-vacuity guard and
       shrinks from two elements to one; the added assertion turns that loss into
       positive evidence for the drop.
     - `tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild` — the
       same two edits: expected set `{"wrapping"}`, plus
       `assert session.get(Topic, "softwrap") is None` taken on the live side
       before the rebuild comparison. Same reason: the set is what keeps a
       `live == rebuilt` comparison of two derived states from passing vacuously.

  2. **Routing to `no-signal` empties a topic too.** Reds against unchanged
     code. New in `tests/test_routing_live.py`:
     `test_route_to_no_signal_drops_the_emptied_topic` — route `I_1` to
     `"softwrap"` as that topic's only item, then route `I_1` to `NO_SIGNAL`;
     assert `session.get(Topic, "softwrap") is None` and
     `session.get(Topic, NO_SIGNAL) is None`. Against unchanged code the first
     fails: the old branch dirties the leaving topic, and the new verdict being
     `NO_SIGNAL` mints nothing, so an empty dirty row is left standing. The
     second assertion pins that the reserved verdict still names no topic. This
     slice is why the change is not confined to movement between topics.

  3. **A topic that keeps a sibling survives, dirty.** A regression guard, and
     named as one — do not prescribe a red for it. New in
     `tests/test_routing_live.py`: `test_reroute_keeps_a_topic_that_keeps_items`
     — route `I_1` and `I_2` to `"softwrap"`, mark `"softwrap"` clean through a
     second session, then re-route `I_1` to `"wrapping"`; assert
     `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)`.
     The cleaning step is load-bearing here and stays: a `Topic` row is born
     dirty, so asserting `dirty is True` against a fresh row would pass with the
     dirtying step removed — the note already at `tests/test_unroute.py:109-111`.
     It is green under unchanged code, because on the surviving-sibling path old
     and new are observationally identical, and mutating `_release_topic` to
     delete unconditionally reds the existing
     `test_unroute_dirties_a_topic_that_keeps_items` as well, so it discriminates
     nothing that test does not. What it adds is narrower and still worth
     pinning: that the *`route`* branch reaches `_release_topic`'s `remaining`
     branch at all.

  4. **A same-topic re-route dirties rather than drops.** Also a guard, and the
     only coverage this path has: `record_verdict` refuses a live re-route, so a
     same-topic re-route is reachable only through re-entry. New in
     `tests/test_routing_live.py`: `test_same_topic_reroute_keeps_the_topic_dirty`
     — route `I_1` to `"softwrap"`, mark it clean, then record a second `route`
     event sending `I_1` to `"softwrap"` again; assert
     `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)`.
     Green under unchanged code. It discriminates a release implemented as
     delete-then-recreate of the `Route` row — the shape `_apply_unroute_event`
     uses, and the one an executor is most likely to reach for — where the
     pending `DELETE` can wipe the row the merge just wrote.

  5. **A topic emptied and re-minted inside one replay session survives.** The
     upsert's autoflush guard. New in `tests/test_routing_replay.py`:
     `test_a_topic_reminted_after_being_emptied_in_one_replay_survives` — build a
     routing ledger under `tmp_path`, not the shared fixture (three test files
     read that one), carrying in order: dispatch and route `I_1` to
     `"softwrap"`; dispatch and route `I_2` to `"wrapping"`; route `I_1` to
     `"wrapping"`, which empties `"softwrap"`; route `I_2` to `"softwrap"`, which
     re-mints it. Rebuild from it in one `Store.rebuild_from_ledgers` call and
     assert `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)`
     and `session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)`.
     Probe-confirmed green under unchanged code, so it is **not** red-phase
     evidence for the upsert. What it discriminates is an upsert issued without
     autoflush — on the connection rather than through `session.execute()` —
     where the pending `DELETE` flushes after the `INSERT` and wipes the
     re-minted row. The cross-process mint race the upsert also closes is closed
     by construction and is not testable without threads; record that rather than
     attempting it.

## Phase 2: gate, docs, and the landing check (type: inline)

- Item 2.1: repository root — run `just precommit` over the whole tree.
  Requirements: NFR8.

  The gate's value here is wider than Phase 1's own tests. `unroute`, `reenter`,
  `requeue` and `close` are unchanged in *behaviour* but not in code:
  `_apply_reenter_event` calls `_mark_topic_dirty` directly and
  `_apply_unroute_event` reaches it through `_release_topic`, so the upsert
  changes what runs under all of them. Their suites — `test_unroute.py`,
  `test_reenter.py`, `test_requeue.py`, `test_cli_close.py` — must come back
  green unchanged, and any edit needed to keep them green is a signal that
  behaviour moved where this job said it would not. `just green` is cached
  against a hash of `src/` and `tests/`, which Phase 1 touched, so it will
  actually run; read its one-line recap, not its silence.

- Item 2.2: `docs/ghmem-decisions.md` — generalize the emptied-topic decision
  and add a separate one for the upsert. Requirements: FR7, NFR5.
  Depends on: Item 1.1

  Two edits, one item, because all edits to one prose artifact land together.

  Retitle the entry at `:120` from **Unrouting a topic's last item deletes the
  `Topic` row.** to **Emptying a topic deletes its `Topic` row.** and generalize
  its body: the invariant "a `Topic` row exists exactly while some `Route`
  carries its name as a verdict other than `no-signal`" now holds
  unconditionally, on `unroute`, on a re-route away, and on a route to
  `no-signal` alike. Delete the "The step is scoped to `unroute`" sentence and
  the "re-routing a topic's last item still leaves an empty dirty row, and that
  state is reachable (Open questions)" clause. Apply to the closing clause the
  same softening as Item 1.1's `_release_topic` docstring — they are one edit
  stated twice; the conclusion stands on replay-destructiveness alone. Record
  here, as the cost this decision knowingly takes on, that an emptied-by-re-route
  topic's `docs/design/<name>.md` is left on disk with `ghmem index` still
  listing it and no dirty row carrying a rewrite, and that this is strictly worse
  than the `unroute` case it generalizes: there the item returns to the queue
  carrying no verdict, so the doc is merely unaccompanied and a later re-triage
  may re-mint the topic, while here the item carries a live verdict naming a
  different topic. No surface reports it — `build_status` reads only the store
  and the fetch ledger and never touches the filesystem.

  Add a **separate** entry titled **`_mark_topic_dirty` is a conditional
  upsert.**; folding it into the one above buries
  a change that closes a pre-existing cross-process race and is
  design-significant on its own. Its argument: the `sqlite3` driver opens a
  transaction before a write but not before a read, so a get-then-add lets two
  workers routing different items into the same not-yet-existing topic both read
  `None`, both queue the `INSERT`, and the loser fail the primary key on flush —
  and because the reducer runs before the ledger append, the loser's verdict
  never lands at all, dying with an opaque `IntegrityError` rather than a
  `TriageSessionError`. That window closed permanently once a topic existed;
  after this change a topic can be emptied and dropped, so it reopens every time,
  which is why the fix belongs in this change rather than after it. Same
  reasoning as `reserve_dispatch`, pointed at rather than restated.

  Interfaces:
  - Retitled decision, cited verbatim by Item 2.3's FR7 Rationale line — bold,
    with `Topic` in backticks: Emptying a topic deletes its `Topic` row.
  - New decision, referred to by Item 2.5's changelog entry — bold, with the
    function name in backticks: `_mark_topic_dirty` is a conditional upsert.

- Item 2.3: `docs/ghmem-design.md` — drop the settled Open question, and give
  FR7 its topic-lifecycle clause. Requirements: FR7.
  Depends on: Item 2.2

  Drop the first Open question (`:250-263`) in full, its closing "Rationale as
  it stands" line included. Its dated observation that the mirror holds zero
  empty `Topic` rows across 95 routes goes with it rather than being restamped:
  situational state does not belong in a design doc, and refreshing the figure
  only schedules the same correction again. The `## Open questions` heading and
  the NFR10 bullet stay.

  In FR7 (`:83-93`), add one clause after the `ghmem requeue` sentence stating
  that a correction which empties a topic deletes its `Topic` row, whichever
  mechanism reached it, and repoint the Rationale cross-reference from
  **Unrouting a topic's last item deletes the `Topic` row** to the retitled
  **Emptying a topic deletes its `Topic` row**. That cross-reference is the only
  citation of the old title outside the dropped bullet; the hits under
  `plans/ghmem-design-migration/reports/` are frozen write-time records and are
  not updated. Do not add the clause to FR4 or FR6.

- Item 2.4: `docs/ghmem-rejected.md` — record the overruled argument.
  Requirements: FR7.
  Depends on: Item 2.2

  Add one bold-titled entry to `## Rejected alternatives`, placed after
  **Relaxing `record_verdict` to accept a live re-route** so the route-correction
  refusals sit together: **Leaving a re-routed topic's emptied row dirty.** It
  was the live rationale — a re-routed item is still in the corpus under another
  name, so the emptied topic's doc has a rewrite to receive and the dirty row is
  what carries it — and it is overruled because `dirty` means "needs
  re-synthesis" and is what `status` dispatches synthesis workers from, so an
  empty dirty row is precisely the `TopicNotRoutedError` breakage the rule exists
  to remove. Preserving an empty dirty `Topic` as a marker for the orphaned doc
  needs a third state distinguishing "synthesize me" from "delete me", which is a
  larger design than this one. Write it as a standalone decision that reads on
  its own terms, not as an account of what the document used to say.

- Item 2.5: `docs/ghmem-changelog.md` — append the dated entry.
  Requirements: FR7, NFR5.
  Depends on: Item 2.2, Item 2.3, Item 2.4

  A reversed decision is design-significant, and the file's convention is
  `**YYYY-MM-DD — <title>.**` followed by a narrative of what changed and why,
  closing with a pointer to the frozen record. Title it **`route` deletes a topic
  it empties.** Narrate: the reducer's `route` branch now releases the topic the
  item leaves, converging on `unroute`, so the `Topic`-row invariant holds
  unconditionally; two consequences the one-line framing hides are that routing
  to `no-signal` from a topic's last item drops it too and that a same-topic
  re-route dirties rather than drops; and `_mark_topic_dirty` became a
  conditional upsert in the same change because a topic that can be dropped
  reopens the mint race a get-then-add leaves open. Name what moved in the
  sibling docs — the Open question dropped from `docs/ghmem-design.md`, the
  overruled argument now in `docs/ghmem-rejected.md` — and close with
  `plans/reroute-empty-topic/outline.md` as the frozen record, which is what that
  file is for. A corpus reading is permitted in this file, which is dated by
  construction unlike the design doc, but take it at write time rather than
  copying the outline's.

- Item 2.6: `corpus/cache.sqlite` — verify the live mirror against the new
  invariant. Requirements: NFR5.
  Depends on: Item 1.1

  The mirror was derived under the old reducer, so it can hold empty `Topic`
  rows the new rule forbids. From the repository root, query it with:

  ```sql
  select t.name from topic t
  where not exists (select 1 from route r where r.verdict = t.name)
  ```

  Zero rows → the mirror already agrees with the new reducer and nothing further
  is needed; report that. One or more rows → report the names and **stop**.
  Bringing them into line means `ghmem rebuild`, which does more than refresh the
  mirror: it also appends a `reenter` event to `corpus/routing.jsonl` for every
  route whose item has moved past its verdict. That is a write to the one corpus
  file a re-sync cannot regenerate, and it is my human partner's call, not the
  orchestrator's. Run the query; do not inherit the outline's reading of it.

## Out of scope

- **The orphan sweep** — `{docs/design/*.md} - {Topic.name}`, in `index` or a
  new `gc`. Nothing detects an orphan today; `build_status` never reads the
  filesystem. Recorded as a named follow-up in the outline, not built here.
- **Filesystem deletion of a synthesized doc.** The reducer is replayed
  wholesale by `rebuild_from_ledgers`, so a filesystem delete inside it would
  make a rebuild destructive.
- **`Snapshot` rows** — untouched, and load-bearing for the orphan sweep as the
  durable record of a synthesized doc.
- **Any ledger schema change.**
- **Extracting `tools/ghmem/tests/routing_helpers.py`** across the five routing
  test files, per the placement decision above.
