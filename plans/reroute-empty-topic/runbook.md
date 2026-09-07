# Runbook — `route` deletes a topic it empties

Design: `plans/reroute-empty-topic/outline.md`. Recall:
`plans/reroute-empty-topic/recall-artifact.md`. Requirements are
`docs/ghmem-design.md`'s FR4, FR6, FR7, NFR5 and NFR8 — this job authors none
of its own. All paths below are relative to the repository root; the Python
package lives under `tools/ghmem/`. Still past NFR9's 400-line budget after a
consolidation pass, and stated rather than absorbed: the change is one atomic
reducer edit whose evidence is one red batch, so splitting the runbook would
separate that batch from the implementation it validates.

## Settled here, against the outline

**Where the new `route` tests live.** Add `_dispatch(item_id)` and
`_route(item_id, verdict, updated_at)` builders to
`tools/ghmem/tests/test_routing_live.py`, mirroring `test_unroute.py:20-45`:
the new tests are `route` tests and belong in the live file, which lacks only a
multi-item builder. Both builders have consumers — slice 1's FR4/FR6 test
mirrors `test_unroute.py:279` and dispatches each item before routing it, and
slices 1 and 2 route several items in one test; slice 4's test builds its own
ledger in `test_routing_replay.py`. Extracting a shared `routing_helpers.py` —
the repo's convention, and five routing test files already duplicate
`_live_store`/`_session` over the same `routing_fetch.jsonl` — is rejected *for
this change*: three of the five (`test_reenter.py`, `test_requeue.py`, and
`test_unroute.py` itself, the builders' source) sit outside this job's
boundaries, so a partial extraction leaves the duplication standing while adding
an import layer. Carried under Out of scope. The module constants `DISPATCH_1`,
`ROUTE_SOFTWRAP` and `ROUTE_WRAPPING` stay — three existing tests read them, and
replacing them is churn.

**FR7 gains a topic-lifecycle clause, and no other requirement does.** Reasoning
with the edit, in Item 2.3.

**The outline's "only deterministic test of the upsert" is not one.** Probed
directly at the recall checkpoint, the within-one-replay-session
emptied-then-re-minted case stays **green under the unchanged get-then-add
`_mark_topic_dirty`**, because `session.get` autoflushes the pending `DELETE`
before the recreate; slice 4 is named for what it actually discriminates. The
outline's red set otherwise reproduced exactly — 5 failed, 273 passed — and
slice 1 adds two further tests that red against unchanged code for the same
reason, so the red batch is seven tests, not five.

## Requirements mapping

| Requirement | Phase | Items | Notes |
|---|---|---|---|
| FR4 — synthesis writes one living document per topic | 1 | 1.1 | Pinned end to end by slice 1's `test_status_dirty_set_stays_assemblable_after_a_reroute`: the empty dirty row `status` offers and `topic.assemble` refuses with `TopicNotRoutedError` is the breakage removed |
| FR6 — the pipeline is incremental | 1 | 1.1 | Same test — every name in `build_status`'s dirty set stays assemblable, which is what makes the dirty set a usable synthesis worklist |
| FR7 — a verdict is correctable | 1, 2 | 1.1, 2.2, 2.3, 2.4, 2.5 | Re-route joins `unroute` in releasing an emptied topic |
| NFR5 — the routing ledger is the system's own memory | 1, 2 | 1.1, 2.2, 2.5, 2.6 | The upsert stops a losing verdict dying before its ledger append; 2.6 guards the one corpus file a re-sync cannot regenerate |
| NFR8 — the toolchain gate is the bar for every change | 2 | 2.1 | `just precommit` over the whole tree, once Phase 1 has landed |

## Phase 1: the reducer converges on `unroute` (type: tdd)

- Item 1.1: `tools/ghmem/src/ghmem/routing_reduce.py` — `_apply_route_event`
  releases the topic it leaves, and `_mark_topic_dirty` becomes a conditional
  upsert. Requirements: FR4, FR6, FR7, NFR5.
  Model: sonnet

  Three edits to the module, plus one docstring clause in
  `tools/ghmem/src/ghmem/ledger.py`. Headroom against the gate's 400-line cap
  (`scripts/check_line_limits.sh`, over `src` and `tests`): `routing_reduce.py`
  is at 337 lines and `tests/test_routing_replay.py` at 340, about 55 each.

  - `_apply_route_event` — capture the pre-merge verdict as `left` before
    `session.merge`, then call `_release_topic(session, left)` **after** the
    merge and after the join-side `_mark_topic_dirty`, when `left` is neither
    `None` nor `NO_SIGNAL`. Two of those three orderings are load-bearing. The
    release follows the merge because `_release_topic` counts the routes still
    carrying the name and relies on autoflush emitting the pending `UPDATE`
    first; called before it, the item still carries the old verdict, the count
    is never zero, and the row is never dropped. `left` is read before the merge
    because `session.get(Route, id)` returns the identity-mapped instance
    `merge` updates in place, so `previous.verdict` afterwards yields the *new*
    verdict. The position relative to the join-side `_mark_topic_dirty` is not
    load-bearing — every ordering of those two produces the same rows — and is
    fixed only so two executors write the same code. The summary line becomes
    `Replace an item's verdict, releasing the topic it leaves.` — 64 characters
    counting the indent and the opening `"""`, inside the 80-character ceiling
    docformatter's 81–88 dead zone imposes, and the only summary line that
    changes.
  - `_release_topic` — delete the paragraph beginning "Scoped to `"unroute"`" in
    full, open-question sentence included, and state the rule as covering
    withdrawal and re-route alike. Reword the autoflush comment: the caller's
    pending write is a `delete` under `unroute` and an `update` under `route`.
    Add the concurrency argument, the stronger reason the ordering matters — the
    autoflush emitting that pending write is where SQLite's writer lock is
    taken, so the `COUNT` and the `DELETE` land on the same side of it and a
    worker routing another item into the same topic cannot commit between them:
    either the count sees the sibling and dirties, or the topic is dropped and
    the sibling's route re-mints it. Soften the `docs/design/<name>.md`
    paragraph: keep the replay-destructiveness argument, which is load-bearing,
    and drop the clause "a synthesized doc also remains true prose about items
    that really were routed there", which re-route falsifies; Item 2.2 makes the
    same edit to the decision entry and records the cost. The `Snapshot`
    paragraph survives unchanged; its argument carries over to `route` as
    written.
  - `_mark_topic_dirty` — becomes a conditional upsert on `Topic` keyed on
    `name`, setting `dirty` true on conflict, issued through the reducer's own
    `session.execute()` and built with `sqlalchemy.dialects.sqlite.insert`
    (imported as `sqlite_insert`, matching `dispatch_store.py:20`). Its
    docstring points at `reserve_dispatch`'s reasoning for why it is an upsert
    rather than restating it, and records that `session.execute()`'s autoflush
    is load-bearing: `rebuild_from_ledgers` replays the whole routing ledger in
    one `Session`, so a topic emptied at one event and re-minted at a later one
    is a within-session delete-then-recreate, and the pending `DELETE` must
    flush before the recreate or it wipes the new row.
  - `tools/ghmem/src/ghmem/ledger.py` — `NO_SIGNAL`'s docstring (`:139-143`)
    says the reserved verdict "marks an item as triaged without creating or
    dirtying any `Topic` row". Literally still true, misleading from here on:
    slice 1's no-signal case is a `route` to `NO_SIGNAL` that *deletes* one. Add
    one sentence — it creates and dirties no topic of its own, but a route to it
    still releases the topic the item leaves — and nothing else in the file. In
    this item, not a doc item: a docstring the behaviour change falsifies moves
    in the same commit as the behaviour.

  Widened deliberately: `sqlite_insert` puts a dialect-specific import in a
  dialect-neutral module. Consistent with `dispatch_store.py`, and the mirror is
  SQLite-only.

  Interfaces:
  - `_apply_route_event(session: Session, data: RouteData) -> None` — signature
    unchanged; post-condition gains "the topic named by the pre-merge verdict is
    released".
  - `_release_topic(session: Session, name: str) -> None` — signature unchanged;
    now reached from the `route` branch as well as `unroute`.
  - `_mark_topic_dirty(session: Session, name: str) -> None` — signature
    unchanged; now a conditional upsert executed through `session.execute()`.
  - `NO_SIGNAL: Final[str] = "no-signal"` (`ledger.py`) — value unchanged; its
    docstring gains the clause above.
  - Shared prose contract, stated once in `_release_topic`'s docstring and
    restated in `docs/ghmem-decisions.md` (Item 2.2): nothing removes
    `docs/design/<name>.md`, because this reducer is replayed wholesale by
    `rebuild_from_ledgers` and a filesystem delete inside it would make a
    rebuild destructive.

  Slices:

  One reducer change greens the whole red set, so slice 1 is the red batch and
  its red phase is one run over all seven tests, not one per observation site.
  Nothing in the batch has an implementation increment of its own — one
  `_release_topic` call produces every one of these observables — so splitting
  it finer would manufacture a second "red" that is already green. Slices 2–4
  are regression guards written *after* that green: none reds against unchanged
  code, none gets a red phase, stated per slice and not an omission.

  1. **An emptied topic is dropped — whether emptied by a re-route away or by a
     route to `no-signal`.** The red batch: the five probe-confirmed assertions,
     plus two new tests that red against unchanged code for the same reason.
     - **New, and the FR4/FR6 contract for this change.** In
       `tests/test_routing_live.py`:
       `test_status_dirty_set_stays_assemblable_after_a_reroute`, the `route`
       counterpart of `test_unroute.py:279` — structurally that test with a
       re-route as its final event. Route `I_1` alone to `"softwrap"` and `I_2`
       and `I_3` to `"wrapping"`, each dispatched first through `_dispatch`,
       then re-route `I_1` to `"wrapping"`. Assert
       `build_status(store, Ledger(FETCH_FIXTURE)).dirty == ("wrapping",)`,
       `.topics == ("wrapping",)`, and that `assemble(store, routing, name)`
       raises nothing for every name in the dirty set. Against unchanged code
       both halves fail — the dirty set still carries `"softwrap"`, and
       `assemble` refuses it with `TopicNotRoutedError`. It asserts on the
       surface that suffers the breakage, and is the only test here pinning FR4
       or FR6.
     - **New.** In `tests/test_routing_live.py`:
       `test_route_to_no_signal_drops_the_emptied_topic` — route `I_1` to
       `"softwrap"` as that topic's only item, then route `I_1` to `NO_SIGNAL`;
       assert `session.get(Topic, "softwrap") is None` and
       `session.get(Topic, NO_SIGNAL) is None`. Against unchanged code the first
       fails: the old branch dirties the leaving topic, and a `NO_SIGNAL` verdict
       mints nothing, leaving an empty dirty row standing. The second pins that
       the reserved verdict still names no topic, and reds if an executor drops
       the `data["verdict"] != NO_SIGNAL` guard on the join-side dirtying. This
       case is why the change is not confined to movement between topics.
     - `tests/test_routing_replay.py::test_reroute_replaces_the_verdict_and_dirties_both_topics`
       → rename to
       `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic`; assert
       `session.get(Topic, "softwrap") is None`,
       `session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)`,
       and the `Route` row equal in full to the fixture's second event: verdict
       `"wrapping"`, note `"actually about wrapping"`, session `"s2"`,
       `item_updated_at` `2026-01-01T00:00:00+00:00`. Delete the docstring
       paragraph beginning
       "The *old*-topic half of the reducer is NOT proven here" **in full**
       rather than rewording it: it documents a limitation this change removes,
       and a rebuild can now distinguish the two.
     - `tests/test_routing_live.py::test_reroute_dirties_the_topic_it_leaves` →
       replaced by `test_reroute_of_the_last_item_drops_the_topic_row`, the live
       counterpart asserting the same `"softwrap"`-gone/`"wrapping"`-dirty pair:
       route `I_1` to `"softwrap"`, then re-route it to `"wrapping"`, both
       through `store.record_routing_event`. Drop the `dirty = False` cleaning
       step — it defeated the born-dirty trap for a `dirty is True` assertion
       and guards nothing against `is None`. Delete the docstring's second
       paragraph, whose claim that "dropping the reducer's old-topic dirtying
       changes nothing observable" this change falsifies.
     - `tests/test_routing_replay.py::test_route_creates_topic_marked_dirty` —
       retarget the topic assertion to `"wrapping"` and rewrite the docstring,
       which names "softwrap" in both its `When:` and its `Then:`. This is
       fixture coupling, not a contract change: the shared ledger re-routes item
       #1 away from `"softwrap"` at its last event. The second assertion,
       `route.item_updated_at.isoformat() == "2026-01-01T00:00:00+00:00"`,
       survives untouched — the fixture's re-route carries the same stamp as the
       original route.
     - `tests/test_routing_replay.py::test_rebuild_is_idempotent` **and**
       `tests/test_routing_live.py::test_live_writes_match_a_cold_rebuild` — the
       same two edits to each: expected set becomes `{"wrapping"}`, **and add**
       `assert session.get(Topic, "softwrap") is None`. In the cold-rebuild test
       that assertion is taken inside the first `_session` block, alongside the
       `live` tuple capture and before the rebuild. In both, the hardcoded set
       is the only thing keeping a comparison of two derived states —
       idempotence in one, `live == rebuilt` in the other — from passing
       vacuously, and it shrinks from two elements to one; the added assertion
       turns that loss into positive evidence for the drop.

  2. **A topic that keeps a sibling survives, dirty.** A regression guard, named
     as one — no red phase, written after slice 1's green. New in
     `tests/test_routing_live.py`: `test_reroute_keeps_a_topic_that_keeps_items`
     — route `I_1` and `I_2` to `"softwrap"`, mark `"softwrap"` clean through a
     second session, then re-route `I_1` to `"wrapping"`; assert
     `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)`. The
     cleaning step is load-bearing here and stays: a `Topic` row is born dirty,
     so asserting `dirty is True` against a fresh row would pass with the
     dirtying step removed — the note already at
     `tests/test_unroute.py:109-111`. It discriminates nothing an existing test
     does not: green under unchanged code, because on the surviving-sibling path
     old and new are observationally identical, and mutating `_release_topic` to
     delete unconditionally reds `test_unroute_dirties_a_topic_that_keeps_items`
     too. What it adds is narrower and still worth pinning — that the *`route`*
     branch reaches `_release_topic`'s `remaining` branch at all.

  3. **A same-topic re-route keeps both the topic and the `Route` row.** Also a
     guard, no red phase, and the only coverage this path has: `record_verdict`
     refuses a live re-route, so a same-topic re-route is reachable only through
     re-entry. New in `tests/test_routing_live.py`:
     `test_same_topic_reroute_keeps_the_topic_and_the_route` — route `I_1` to
     `"softwrap"`, mark it clean, then record a second `route` event sending
     `I_1` to `"softwrap"` again, **carrying a different session and note than
     the first** so the merge is observable rather than a replayed no-op; assert
     `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)`
     *and* the `Route` row equal in full to the second event. The `Route`
     assertion is the load-bearing half: the topic assertion alone cannot fail,
     because the join-side `_mark_topic_dirty` on the new verdict dirties
     `"softwrap"` under every implementation in the plausible space, including
     one that never reaches `_release_topic` — over-determined, in
     `memory/ddaanet/green-is-not-evidence.md`'s sense. The `Route` row moves
     under the failure this slice catches: a release written as the
     delete-then-recreate `_apply_unroute_event` uses, where the pending
     `DELETE` can wipe or collide with the row the merge just wrote.

  4. **A topic emptied and re-minted inside one replay session survives.** The
     upsert's autoflush guard, and a guard only — no red phase, probe-confirmed
     green under unchanged code, so **not** evidence for the upsert. New in
     `tests/test_routing_replay.py`:
     `test_a_topic_reminted_after_being_emptied_in_one_replay_survives`.

     That file has no event builders and must not take the shared fixture
     (`routing_events.jsonl` is read by `test_routing_replay.py`,
     `test_requeue.py` and `test_dispatch_store.py`), and the placement decision
     above covers `test_routing_live.py` only. So build the ledger in the test:
     `Ledger[RoutingEvent](tmp_path / "reroute_routing.jsonl")` and `append`
     four inline `route` events — `I_1` to `"softwrap"`; `I_2` to `"wrapping"`;
     `I_1` to `"wrapping"`, which empties `"softwrap"`; `I_2` to `"softwrap"`,
     which re-mints it. No `dispatched` events: the reducer routes an item with
     no prior dispatch, as `test_reroute_dirties_the_topic_it_leaves` already
     relies on. Then `Store.open(tmp_path / DB_NAME)` and one
     `rebuild_from_ledgers` call against `Ledger(FETCH_FIXTURE)`, and assert
     `session.get(Topic, "softwrap") == Topic(name="softwrap", dirty=True)` and
     `session.get(Topic, "wrapping") == Topic(name="wrapping", dirty=True)`.

     What it discriminates is an upsert issued without autoflush — on the
     connection rather than through `session.execute()` — where the pending
     `DELETE` flushes after the `INSERT` and wipes the re-minted row. That is
     the test's whole justification, so **run it**: apply the non-autoflushing
     variant, confirm this test alone reds, revert, and record the result beside
     the test. An unrun discrimination claim of exactly this shape was already
     wrong once here. The cross-process mint race the upsert also closes is
     closed by construction and is not testable without threads; record that
     rather than attempting it.

## Phase 2: gate, docs, and the landing check (type: inline)

- Item 2.1: repository root — run `just precommit` over the whole tree.
  Requirements: NFR8.
  Depends on: Item 1.1

  The gate's value here is wider than Phase 1's own tests. `unroute`, `reenter`,
  `requeue` and `close` are unchanged in *behaviour* but not in code —
  `_apply_reenter_event` calls `_mark_topic_dirty` directly,
  `_apply_unroute_event` reaches it through `_release_topic` — so the upsert
  changes what runs under all of them, and their suites (`test_unroute.py`,
  `test_reenter.py`, `test_requeue.py`, `test_cli_close.py`) must come back
  green unchanged: any edit needed to keep them green is a signal that behaviour
  moved where this job said it would not. The two plausible fixture casualties
  are expected green unchanged too — `test_status.py` reads its own
  `status_routing.jsonl`, which carries no re-route, and
  `test_dispatch_store.py` reads the shared `routing_events.jsonl` but asserts
  only `dispatched_unrouted() == [I_3]`, which no topic row feeds. Either going
  red means the change reached past what the probe measured.

  `just green` is cached against the interpreter and tool versions and every
  file under `justfile`, `pyproject.toml`, `uv.lock`, `scripts/`, `src/`,
  `tests/` and `hooks/`; Phase 1 touches `src/` and `tests/`, so the cache
  misses and the gate runs. Read its one-line recap, not its silence. Phase 2's
  doc edits are outside those inputs, which is why the gate runs here rather
  than last.

- Item 2.2: `docs/ghmem-decisions.md` — generalize the emptied-topic decision
  and add a separate one for the upsert. Requirements: FR7, NFR5.
  Depends on: Item 1.1

  Two edits, one item: all edits to one prose artifact land together.

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
  the cost this decision knowingly takes on: an emptied-by-re-route topic's
  `docs/design/<name>.md` is left on disk, `ghmem index` still lists it, and no
  dirty row carries a rewrite — strictly worse than the `unroute` case it
  generalizes, where the item returns to the queue carrying no verdict, so the
  doc is merely unaccompanied and a later re-triage may re-mint the topic. No
  surface reports it: `build_status` reads only the store and the fetch ledger,
  never the filesystem.

  Add a **separate** entry titled **`_mark_topic_dirty` is a conditional
  upsert.**; folding it into the one above buries a change that closes a
  pre-existing cross-process race and is design-significant on its own. Its
  argument: the `sqlite3` driver opens a transaction before a write but not
  before a read, so a get-then-add lets two workers routing different items into
  the same not-yet-existing topic both read `None`, both queue the `INSERT`, and
  the loser fail the primary key on flush — and because the reducer runs before
  the ledger append, the loser's verdict never lands at all, dying with an
  opaque `IntegrityError` rather than a `TriageSessionError`. That window closed
  permanently once a topic existed; after this change a topic can be emptied and
  dropped, so it reopens every time, which is why the fix belongs here rather
  than after. Same reasoning as `reserve_dispatch`, pointed at not restated.

  Interfaces:
  - Retitled decision, cited verbatim by Item 2.3's FR7 Rationale line — bold,
    with `Topic` in backticks: Emptying a topic deletes its `Topic` row.
  - New decision, referred to by Item 2.5's changelog entry — bold, with the
    function name in backticks: `_mark_topic_dirty` is a conditional upsert.

- Item 2.3: `docs/ghmem-design.md` — drop the settled Open question, and give
  FR7 its topic-lifecycle clause. Requirements: FR7.
  Depends on: Item 2.2

  Drop the first Open question in full, `:250-264` — the bullet ends at `:264`,
  not at the "Rationale as it stands:" line opening at `:263`, so do not
  truncate it. Its dated observation that the mirror holds zero empty `Topic`
  rows across 95 routes goes with it rather than being restamped: situational
  state does not belong in a design doc, and refreshing the figure only
  schedules the same correction again. The `## Open questions` heading and the
  NFR10 bullet stay.

  In FR7 (`:82-93`), add one clause after the `ghmem requeue` sentence stating
  that a correction which empties a topic deletes its `Topic` row, whichever
  mechanism reached it, and repoint the Rationale cross-reference from
  **Unrouting a topic's last item deletes the `Topic` row** to the retitled
  **Emptying a topic deletes its `Topic` row**. That cross-reference is the only
  citation of the old title outside the dropped bullet; the hits under
  `plans/ghmem-design-migration/reports/` are frozen write-time records and are
  not updated, and `tools/ghmem/tests/test_unroute.py:136` only echoes the old
  wording as a test docstring describing `unroute`'s own behaviour, which this
  change does not touch — leave it. The clause is needed because FR7 as written
  says what each correction mechanism does to the *verdict* and nothing about
  the `Topic` row, so retitling its pointer alone would leave FR7 describing a
  distinction between the three corrections that this change removes. Do not add
  it to FR4 or FR6: FR6's subject is incrementality and FR4's is the doc form,
  so either would just duplicate the invariant.

- Item 2.4: `docs/ghmem-rejected.md` — record the overruled argument.
  Requirements: FR7.
  Depends on: Item 2.2

  Add one bold-titled entry to `## Rejected alternatives`, after **Relaxing
  `record_verdict` to accept a live re-route** so the route-correction refusals
  sit together: **Leaving a re-routed topic's emptied row dirty.** It was the
  live rationale — a re-routed item is still in the corpus under another name,
  so the emptied topic's doc has a rewrite to receive and the dirty row carries
  it — and it is overruled because `dirty` means "needs re-synthesis" and is
  what `status` dispatches synthesis workers from, so an empty dirty row is
  precisely the `TopicNotRoutedError` breakage the rule exists to remove.
  Keeping one as a marker for the orphaned doc needs a third state
  distinguishing "synthesize me" from "delete me", a larger design than this.
  Write it as a standalone decision that reads on its own terms, not as an
  account of what the document used to say.

- Item 2.5: `docs/ghmem-changelog.md` — append the dated entry.
  Requirements: FR7, NFR5.
  Depends on: Item 2.2, Item 2.3, Item 2.4

  A reversed decision is design-significant, and the file's convention is a
  top-level list item opening `- **YYYY-MM-DD — <title>.**`, followed by a
  narrative of what changed and why, closing with a bare path to the frozen
  record. Date it the day the change lands (`date -I`), not the day this runbook
  was written, and title it **`route` deletes a topic it empties.** Narrate: the
  reducer's `route` branch now releases the topic the item leaves, converging on
  `unroute`, so the `Topic`-row invariant holds unconditionally; the two
  consequences the one-line framing hides, that a route to `no-signal` from a
  topic's last item drops it too and that a same-topic re-route dirties rather
  than drops; and the conditional upsert, in the same change because a topic
  that can be dropped reopens the mint race a get-then-add leaves open. Name
  what moved in the sibling docs — the Open question dropped from
  `docs/ghmem-design.md`, the overruled argument now in `docs/ghmem-rejected.md`
  — and close with `plans/reroute-empty-topic/outline.md` as the frozen record.
  A corpus reading is permitted in this file, dated by construction unlike the
  design doc, but take it at write time rather than copying the outline's.

- Item 2.6: `corpus/cache.sqlite` — verify the live mirror against the new
  invariant. Requirements: NFR5.
  Depends on: Item 1.1

  The mirror was derived under the old reducer, so it can hold empty `Topic`
  rows the new rule forbids. There is no `sqlite3` binary on this box, so the
  query runs through the stdlib module; `mode=ro` is what keeps it a read
  against the live mirror. Verbatim, from the repository root — the runbook's
  one code block, because it is a command to run rather than code to copy:

  ```
  python3 -c "
  import sqlite3
  c = sqlite3.connect('file:corpus/cache.sqlite?mode=ro', uri=True)
  print(c.execute('select t.name from topic t where not exists '
                  '(select 1 from route r where r.verdict = t.name)').fetchall())
  "
  ```

  Zero rows → the mirror already agrees with the new reducer and nothing further
  is needed; report that. One or more rows → report the names and **stop**.
  Bringing them into line means `ghmem rebuild`, which does more than refresh
  the mirror: it also appends a `reenter` event to `corpus/routing.jsonl` for
  every route whose item has moved past its verdict — a write to the one corpus
  file a re-sync cannot regenerate, and my human partner's call, not the
  orchestrator's. Run the query; do not inherit the outline's reading of it.

## Out of scope

- **The orphan sweep** — `{docs/design/*.md} - {Topic.name}`, in `index` or a
  new `gc`. Nothing detects an orphan today; `build_status` never reads the
  filesystem. A named follow-up in the outline, not built here.
- **Filesystem deletion of a synthesized doc**, for the replay-destructiveness
  reason stated in Item 1.1's Interfaces block.
- **`Snapshot` rows** — untouched, and load-bearing for the orphan sweep as the
  durable record of a synthesized doc.
- **Any ledger schema change.**
- **Extracting `tools/ghmem/tests/routing_helpers.py`** across the five routing
  test files, per the placement decision above.
- **`docs/ghmem-harness.md`.** Its `ghmem route` entry says the command "marks
  the topic dirty", which stays true of the topic the item *joins*, and the doc
  carries no topic-lifecycle clause on `ghmem unroute` either — that lifecycle
  lives in `docs/ghmem-decisions.md`, which Item 2.2 owns, and adding one to
  `route` alone would put the two command contracts out of step. Considered and
  left; addressed instead by items 2.2 and 2.3.
