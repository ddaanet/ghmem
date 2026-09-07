# Phase 1 — boundary review (whole-phase corrector)

Diff under review: `git diff c7e842a..HEAD` restricted to
`tools/ghmem/src/ghmem/routing_reduce.py`,
`tools/ghmem/src/ghmem/ledger.py`,
`tools/ghmem/tests/test_routing_live.py`,
`tools/ghmem/tests/test_routing_replay.py`.

`git diff --stat c7e842a..HEAD -- tools/ghmem/` names exactly those four files
and no others, so the "green unchanged" set (`test_unroute.py`,
`test_reenter.py`, `test_requeue.py`, `test_cli_close.py`,
`test_dispatch_store.py`) was not edited — the runbook's own signal that
behaviour did not move where it said it would not.

**Verdict: pass, two fixes applied.** Both are docstring corrections; no
assertion, fixture, signature or behaviour was touched.

---

## 1. The change as a whole against Item 1.1's Interfaces block — pass

| Interface | Stated | Found |
|---|---|---|
| `_apply_route_event(session, data) -> None` | signature unchanged; post-condition gains "the topic named by the pre-merge verdict is released" | `routing_reduce.py:123` — signature identical; `left = previous.verdict if previous is not None else None` before `session.merge`, `_release_topic(session, left)` as the last statement under `if left is not None and left != NO_SIGNAL` |
| `_release_topic(session, name) -> None` | signature unchanged; reached from the `route` branch as well as `unroute` | unchanged signature; both call sites present |
| `_mark_topic_dirty(session, name) -> None` | signature unchanged; now a conditional upsert through `session.execute()` | unchanged signature; `sqlite_insert(Topic).values(...).on_conflict_do_update(index_elements=["name"], set_={"dirty": True})` issued as `session.execute(stmt)` |
| `NO_SIGNAL` (`ledger.py`) | value unchanged; docstring gains the release clause | value unchanged; clause added at `ledger.py:143-145`, and nothing else in that file changed |
| Shared prose contract on `docs/design/<name>.md` | stated once in `_release_topic`'s docstring | present, with the replay-destructiveness argument kept and the "remains true prose" clause dropped |

Both load-bearing orderings hold, and I re-derived each from the emitted SQL
rather than from reading:

- **Release after the merge.** The trace for a re-route shows
  `SELECT route (I_1)` → `UPDATE route` → `INSERT INTO topic ... ON CONFLICT`
  → `SELECT count(*) FROM route WHERE verdict = 'softwrap'` → `DELETE FROM
  topic`. The pending `UPDATE` is emitted by the join-side
  `_mark_topic_dirty`'s `session.execute` autoflush, so the `COUNT` sees the
  item already carrying its *new* verdict and returns 0. Called before the
  merge the count would be 1 and the row would never drop.
- **`left` captured before the merge.** `left` binds the `str`, not the ORM
  instance, so the identity-mapped update `merge` performs in place cannot
  reach it.

The `_release_topic` comment's account of *which* statement flushes the
pending write is accurate in both branches: at the join-side
`session.execute` when the route has one, and at the `COUNT` otherwise (the
route-to-`NO_SIGNAL` case, and `unroute`). Verified in the trace.

## 2. Cross-slice test coherence — pass, one redundancy reported

Ten tests, all accounted for. Eight collected in `test_routing_live.py`,
twelve in `test_routing_replay.py`.

**Gaps: none found.** I enumerated the observables Item 1.1 moved and named
the test that reads each:

| Observable | Read by |
|---|---|
| re-route empties → `Topic` row gone (live, one event at a time) | `test_reroute_of_the_last_item_drops_the_topic_row` |
| re-route empties → `Topic` row gone (whole-ledger replay) | `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic` |
| route to `NO_SIGNAL` empties → row gone | `test_route_to_no_signal_drops_the_emptied_topic` |
| `NO_SIGNAL` still mints no topic (join-side guard) | same test, second assertion |
| the `route` branch reaches `_release_topic`'s `remaining` branch | `test_reroute_keeps_a_topic_that_keeps_items` |
| same-topic re-route keeps topic *and* `Route` | `test_same_topic_reroute_keeps_the_topic_and_the_route` |
| `status`'s dirty set stays assemblable (FR4/FR6) | `test_status_dirty_set_stays_assemblable_after_a_reroute` |
| whole-table `Topic` set after the change | `test_rebuild_is_idempotent`, `test_live_writes_match_a_cold_rebuild` |
| `on_conflict_do_update` actually *sets* `dirty` (vs `DO NOTHING`) | `test_reroute_keeps_a_topic_that_keeps_items` and `test_same_topic_reroute_...`, both of which clean the row first and then require `dirty is True` |
| `left` read pre-merge / release post-merge | both orderings red `test_reroute_of_the_last_item_...` and the replay twin |

A route to `NO_SIGNAL` from a topic that *keeps* a sibling has no test, and
should not: old and new code are observationally identical on that path
(`_release_topic`'s `remaining` branch dirties exactly as the old
unconditional `_mark_topic_dirty` did).

**Redundancy found — reported, not fixed.**
`test_routing_replay.py::test_route_creates_topic_marked_dirty` is now
*fully subsumed* by `test_reroute_replaces_the_verdict_and_drops_the_emptied_topic`
in the same file. Both read the same `store` fixture; the former asserts
`topic is not None`, `topic.dirty is True`, `route is not None` and
`route.item_updated_at.isoformat() == "2026-01-01T00:00:00+00:00"`, and the
latter asserts `wrapping == Topic(name="wrapping", dirty=True)` and the whole
`Route` row including that same `item_updated_at`. Every clause of the first
is entailed by the second, so no single fault can red the first while the
second passes. Before slice 1 it was not subsumed — it pinned `"softwrap"`,
which the other test does not name positively.

I did not delete it. The runbook prescribes its retarget explicitly and
argues it is fixture coupling worth keeping; deleting a test the plan names
is a scope call, not a correction. Its *name* is still accurate — the
fixture's `"wrapping"` really is minted by an item routed to a topic that
does not yet exist, even though the event doing it is a re-route — so this is
not a `green-is-not-evidence` name-claims-coverage defect, only redundancy.

## 3. Docstring truth across the phase — two defects found and fixed

Swept all four files in their final state.

**Fix 1 (major) — `routing_reduce.py`, `_mark_topic_dirty` overclaimed.**
The docstring said `session.execute()` was used "because its autoflush is
load-bearing here too", and that "the pending `DELETE` must flush before this
upsert or it wipes the row just recreated". M4 demonstrably refutes
"load-bearing": a connection-issued upsert loses no row anywhere in this
reducer. Rewritten to state the mechanism (which is real — see check 5) and
then say plainly that it is a guard rather than what holds the path up,
naming the two autoflushes that already intervene. The `reserve_dispatch`
race paragraph is untouched, since that reasoning stands.

**Fix 2 (major) — `test_routing_live.py`,
`test_same_topic_reroute_keeps_the_topic_and_the_route` stated a false
mechanism.** Its M3 paragraph said "SQLAlchemy orders the delete before the
insert for the same primary key within one flush either way". That is not
what happens: the unit of work *collapses* a same-primary-key delete-and-add
within one flush into a single `UPDATE`, emitting neither a `DELETE` nor an
`INSERT` (SQL trace in check 5). Corrected to the observed behaviour. The
honest "does not discriminate, recorded rather than engineered into a red"
conclusion is unchanged, and no assertion was touched.

Checked and left alone:

- `apply_routing_event`'s `"route"` paragraph — rewritten in slice 1 and
  accurate.
- `_release_topic`'s docstring and autoflush comment — accurate in both
  branches, including the SQLite writer-lock argument (the transaction is
  deferred, the lock is taken at the flushed write, and the `COUNT` and
  `DELETE` follow it).
- `ledger.py`'s `NO_SIGNAL` clause — accurate.
- `test_a_topic_reminted_after_being_emptied_in_one_replay_survives` — its
  mutation paragraph names `session.merge`'s autoflush as the reason M4 does
  not red. I verified that independently and it is **correct**; no overclaim,
  so I left the whole docstring untouched per check 5's instruction.
- `test_reroute_keeps_a_topic_that_keeps_items` — its M2 claim ("reds this
  test alone") matches the matrix and is the one discriminating mutation.

**Not edited, flagged instead.** `_apply_route_event` carries a one-line
docstring, so neither load-bearing ordering is explained at its own site —
the `left`-before-merge reason lives nowhere in the code at all. The runbook
deliberately scoped that docstring to the summary line, and both orderings
are test-discriminated (each reds two tests), so I left it rather than
overriding a stated plan decision. Worth a sentence if Phase 2 or a later
job reopens the file.

## 4. `green-is-not-evidence` shapes — all three guards intact

- `test_rebuild_is_idempotent` — `{t.name for t in topics} == {"wrapping"}`
  survives as the non-vacuity guard, alongside `len(routes) == 2` and
  `len(dispatches) == 3`. Shrunk from two elements to one and still non-empty,
  which is what the guard needs; `all(t.dirty for t in topics)` cannot go
  vacuous behind it.
- `test_live_writes_match_a_cold_rebuild` — `len(routes) == 1`,
  `len(dispatches) == 1` and `{t.name for t in topics} == {"wrapping"}` all
  precede `live == rebuilt`. Intact.
- `test_reroute_keeps_a_topic_that_keeps_items` — the `dirty = False`
  cleaning step through a second session is present and load-bearing; M2
  reds this test and only this test, which is the positive evidence that the
  born-dirty trap is defeated.

The runbook's instruction *not* to add a separate
`session.get(Topic, "softwrap") is None` to the two set comparisons (which
would only restate the line above it) was followed — neither test carries one.

## 5. The two non-discriminating mutations — evidence for the decision

Everything below is probe evidence, run in memory against
`sqlalchemy 2.0.51` with a `before_cursor_execute` listener printing the
emitted SQL. No file under `tools/ghmem/src/` was written to: the reducer was
mutated by rebinding `routing_reduce._mark_topic_dirty` from a `$TMPDIR`
script, which the internal call sites pick up because they resolve through
module globals. The probe scripts were deleted afterwards.

### M4 — is `session.execute()`'s autoflush ever load-bearing?

**The mechanism is real.** Isolated to its smallest form — a pending ORM
`Topic` `DELETE`, then the same upsert issued on the connection:

```
== C: ORM-pending Topic DELETE + upsert issued on the CONNECTION (no flush)
   rows: []
    INSERT INTO topic (name, dirty) VALUES (?, ?) ON CON ('x', 1, 1)
    DELETE FROM topic WHERE topic.name = ? ('x',)
== D: same, but through session.execute() (autoflush)
   rows: [('x', True)]
    DELETE FROM topic WHERE topic.name = ? ('x',)
    INSERT INTO topic (name, dirty) VALUES (?, ?) ON CON ('x', 1, 1)
```

The row is genuinely lost under C. So the docstring's scenario is not
imaginary; the question is only whether a ledger can reach it.

**It cannot, and the reason is structural rather than incidental.** Three
findings together close it:

1. **`Session.merge(load=True)` autoflushes unconditionally.** From the
   installed source (`sqlalchemy 2.0.51`, `Session.merge`):

   ```python
   if load:
       # flush current contents if we expect to load data
       self._autoflush()
   ```

   It is not conditional on an identity-map miss. I confirmed behaviourally
   too: holding strong references to every `Route` across the whole replay
   (so `session.get` hits the identity map and emits no SQL, and cannot
   autoflush) still puts `DELETE FROM topic ('softwrap')` ahead of the
   connection-issued `INSERT`, and the row survives. This corrects a detail
   worth knowing: the flush that saves slice 4's scenario comes from
   `session.merge`, not from `session.get` — `session.get` autoflushes only
   when it must emit a `SELECT`, which in the reducer it happens to do,
   because SQLAlchemy's identity map is *weak* and the reducer keeps no
   reference to the row it merged. Relying on that alone would be
   incidental; `merge`'s autoflush is not.

2. **`_apply_reenter_event` cannot reach the hazard**, even though it calls
   `_mark_topic_dirty` directly with no merge in front of it. It dirties
   `route.verdict` for a `Route` that exists. But `_release_topic` only
   deletes a `Topic` when its `COUNT` — itself an autoflushed query — returns
   zero routes carrying that name. So a pending `Topic X` `DELETE` implies no
   `Route` carries verdict `X` at that moment; for a later reenter to dirty
   `X`, some `_apply_route_event` must first write a route to `X`, and its own
   `session.merge` flushes the `DELETE` on the way in. I ran this ordering
   (`route I_1→softwrap`, `route I_2→softwrap`, `route I_1→wrapping`,
   `reenter I_2`) under the connection-issued upsert: both topics survive.

3. **`_release_topic`'s own `remaining` branch is safe by construction** — it
   is reached only after the `COUNT` autoflushes, and only for a name whose
   count was non-zero, so no `DELETE` for that name can be pending.

I also ran `route I_1→softwrap`, `route I_2→wrapping`, `unroute I_1`,
`route I_2→softwrap` — the `unroute`-shaped variant of slice 4 — under both
upsert forms. Same rows either way.

**Bottom line for the decision.** There is no ledger-reachable scenario in
which `session.execute()`'s autoflush saves a row, so
`test_a_topic_reminted_after_being_emptied_in_one_replay_survives` cannot be
repaired by picking a different event ordering: the guarantee is supplied by
`merge`, one layer below anything a fixture controls. The two honest options
the runbook names both stand, and the choice between them is a judgement about
what the test is *for*:

- **Drop the test.** It names the upsert and pins nothing about it. What it
  does still pin — a topic emptied and re-minted within one replay ends up
  present and dirty — is genuinely new coverage that no other test has, so
  dropping it loses that.
- **Keep it, renamed off the upsert.** It would then be an honest replay-level
  guard on the emptied-then-re-minted sequence, with a docstring saying the
  autoflush it once claimed to cover is supplied by `merge` instead.

Either way `session.execute()` should stay: it costs nothing, and it is the
form that does not depend on a caller's flush happening to intervene. I have
already weakened the docstring to claim exactly that and no more.

### M3 — can a delete-then-recreate release wipe or collide?

**No. The failure mode the runbook hypothesised cannot occur through this
ORM.** SQLAlchemy's unit of work recognises a delete and an add of the same
primary key within one flush and collapses them into a single `UPDATE`:

```
== A: ORM delete + add, same Topic PK, one flush
   rows: [('x', True)]
    UPDATE topic SET dirty=? WHERE topic.name = ? (1, 'x')
== B: ORM delete + add, same Route PK, one flush
   rows: [('I_1', 'y')]
    UPDATE route SET verdict=?, note=?, session=?, item_ ('y', 'n', 's1', ...)
```

No `DELETE` and no `INSERT` are emitted at all, for either table. There is
nothing to order wrongly and nothing to collide. This holds for reading A
(`_release_topic`'s own `Topic` handling) and reading B (the `Route`
replacement in `_apply_route_event`, mirroring `_apply_unroute_event`) alike,
which is why the s2–4 executor found both green: the mutation does not change
the emitted SQL in the way the prose assumed.

The one construction that *does* produce the described failure is probe C
above — and that is M4's construction, not M3's: it requires the insert to
bypass the unit of work entirely. So M3 and M4 are the same mechanism seen
twice, and it is reachable only by leaving the ORM.

**Bottom line for the decision.** `test_same_topic_reroute_keeps_the_topic_and_the_route`'s
`Route`-row assertion was justified by a defect that cannot exist, so the
`Route` half is not load-bearing against any mutation I can construct. The
test is not thereby worthless — its *topic* half plus the `Route` half
together are the only coverage of the same-topic re-route path at all, which
`record_verdict` refuses live and only re-entry reaches — but the runbook's
stated reason for the `Route` assertion should be retired rather than
restated. I have corrected the docstring's account of the ORM's behaviour and
left the test itself alone.

## 6. Line-count cap — pass, with headroom

`scripts/check_line_limits.sh` caps at 400 over `find src tests -name '*.py'`.
After my edits:

```
362 tools/ghmem/src/ghmem/routing_reduce.py   (+9)
331 tools/ghmem/tests/test_routing_live.py    (+1)
365 tools/ghmem/tests/test_routing_replay.py  (unchanged)
```

**Phase 2 fits, and the question does not really arise for it.** Items 2.2–2.5
edit `docs/` only, 2.6 runs a read-only query against `corpus/cache.sqlite`,
and 2.1 runs the gate. None of them writes a file under `src/` or `tests/`, so
no Phase 2 item can approach the cap. The 35 lines of replay headroom the
brief flagged are not spent by anything Phase 2 does.

## 7. Lifecycle audit — clean

- `git status --short --untracked-files=all -- tools/ghmem/` lists only the two
  files I edited. No saved copy of a mutated source file, no stray fixture, no
  `.orig`/`.bak`/`.rej` anywhere under `tools/ghmem/`.
- `.git/MERGE_HEAD` absent; `git diff --cached --stat` empty — nothing staged.
- `git diff -- tools/ghmem/src/` against HEAD before my edits was empty, so the
  s2–4 mutation runs left nothing behind; the only `src/` delta now is fix 1.
- My own probes ran entirely from `$TMPDIR`, monkeypatching module globals in
  memory. `$TMPDIR/probe` deleted.
- `tools/ghmem/tmp/` holds only the pre-existing `.green-sentinel`,
  `heat.csv`, `heat.err` and `tailsample/`.

## `docs/` findings deferred to Phase 2

Swept `docs/` for statements Phase 1 falsified. Every one is already owned by
a planned Phase 2 item; **nothing new was found**, and I edited nothing.

- `docs/ghmem-decisions.md:120-133` — the whole entry. Its title, the "The step
  is scoped to `unroute`" sentence, the "holds on the `unroute` path alone"
  clause, the "re-routing a topic's last item still leaves an empty dirty row"
  clause, and the closing "a synthesized doc is prose about items that really
  were routed there" are all now false. **Item 2.2.**
- `docs/ghmem-design.md:250-264` — the first Open question, false in full,
  including its sub-claim "Three tests in `test_routing_replay.py` and two in
  `test_routing_live.py` pin the current `route` behaviour" (those tests were
  retargeted) and its dated zero-empty-rows observation. **Item 2.3.**
- `docs/ghmem-design.md:89-91` — FR7's Rationale cites **Unrouting a topic's
  last item deletes the `Topic` row** by its old title, and FR7's prose still
  distinguishes the three corrections only by what they do to the *verdict*.
  **Item 2.3.**
- No doc records `_mark_topic_dirty`'s shape at all, so the upsert falsifies
  nothing in `docs/` — it is an addition, which is what **Item 2.2**'s new
  entry and **Item 2.5**'s changelog line are for.
- Remaining `docs/` hits for `_mark_topic_dirty` and "get-or-create" are under
  `docs/superpowers/plans/` — frozen write-time plan records, correctly left.
- `docs/ghmem-harness.md` re-checked: its `ghmem route` entry says the command
  "marks the topic dirty", still true of the topic the item *joins*. Nothing
  there is falsified, matching the runbook's Out-of-scope reasoning.

## Fixes applied

1. `tools/ghmem/src/ghmem/routing_reduce.py` — `_mark_topic_dirty` docstring:
   dropped the "its autoflush is load-bearing here too" claim, kept the
   mechanism (which probe C confirms), and stated on the evidence that it is a
   guard, naming the two autoflushes that already intervene.
2. `tools/ghmem/tests/test_routing_live.py` —
   `test_same_topic_reroute_keeps_the_topic_and_the_route` docstring:
   replaced "SQLAlchemy orders the delete before the insert for the same
   primary key within one flush" with what the SQL trace shows, that the unit
   of work collapses the pair into a single `UPDATE` and emits neither
   statement.

Nothing else was changed. No assertion, fixture, name, signature or behaviour
was touched; nothing committed.

## Gate

`just precommit` from the repository root, after both fixes:

```
✓ ruff check · docformatter -c · mypy · 283 passed · 47 bats · line limits
```
