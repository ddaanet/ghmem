# Fix report: code findings

All five fixes applied. `just green` is green.

## Applied

### F1 — six stale `ingest.py` prose references

Each repointed to the symbol's real home:

- `src/ghmem/triage.py:306` — `ingest.apply_routing_event` →
  `routing_reduce.apply_routing_event`. The bullet's tail was rewrapped
  to ~72 columns; no wording changed beyond the dotted path.
- `src/ghmem/exceptions.py:148` (was `:147`) —
  `ingest.apply_routing_event` → `routing_reduce.apply_routing_event`
  in `TriageSessionError`'s docstring.
- `src/ghmem/exceptions.py:7-9` — `UnknownRoutingEventError` "belongs
  to ledger replay (`ingest.py`)" → "belongs to the routing reducer
  (`routing_reduce.py`)". The routing-table entry was corrected, not
  dropped.
- `src/ghmem/exceptions.py:16-19` — same correction for
  `InvalidItemUpdatedAtError`.
- `src/ghmem/exceptions.py:93` — "`ingest.py`'s parse guard" → the
  named symbol `routing_reduce._parse_item_updated_at`.
- `src/ghmem/ledger.py:214` — same, in `RouteData.item_updated_at`'s
  docstring.

**The five leave-alone references, checked.** All five read as the
review described in one case and *not* in two others:

- `models.py:9` ("`ingest.py` owns turning raw GraphQL JSON into these
  entities and replaying them into SQLite") — half stale: parsing lives
  in `raw_nodes.py`, replay really is `ingest.py`'s. Pre-existing
  drift, not mine to edit. Left alone.
- `models.py:136` ("every raw-node parser in `ingest.py`") — stale;
  the parsers are the `_Raw*` classes in `raw_nodes.py`. Pre-existing
  drift, not mine to edit. Left alone.
- `status.py:3` ("the routing tables `ingest.py` replays into") —
  correct. `ingest.rebuild_from_ledgers` is the replay driver.
  Left alone.
- `ledger.py:86` ("`ingest.py`'s `_RawNode` subclasses annotate
  `_event_type`") and `ledger.py:99` ("the shape of a raw node is
  `ingest.py`'s contract (its `_Raw*` structs)") — **these two are
  stale, contrary to the brief.** `grep -n "_RawNode\|_event_type"
  src/ghmem/*.py` puts every `_Raw*` subclass and every `_event_type`
  annotation in `raw_nodes.py`; `ingest.py` only imports
  `_RAW_BY_TYPE` from it (`ingest.py:45`) and `ingest.py`'s own module
  docstring says so at `:10-11` ("The raw-node contract those replay …
  lives in `raw_nodes.py`"). Same pre-existing `raw_nodes.py`-split
  drift as the two `models.py` hits, not this slice's. `ledger.py` is
  mine to edit, but the brief said leave these alone, so I did not
  touch them. Flagging rather than fixing silently.

**Full `src/` sweep for `ingest`** (`grep -rn ingest src/`) — hits not
on either list, all correct as written, all left alone:
`heat_query.py:8`, `ingest.py:1/27/34/45/86/89/103/111`,
`ledger.py:25` ("bulk ingestion", not the module),
`models.py:96`, `raw_nodes.py:44`, `raw_nodes_core.py:6`,
`routing_reduce.py:4/9`, `store.py:6/52/106`,
`sync_core.py:10/17/18/33/144/154`.

### F2 — `ghmem unroute`'s not-found message

`src/ghmem/cli.py:118-127`. The list comprehension was replaced by an
explicit loop so the handler holds the failing number:

```python
    item_ids: list[GraphQLId] = []
    for number in numbers:
        try:
            item_ids.append(store.get_issue_or_pr(IssueOrPRNumber(number)).id)
        except IssueOrPRNotFoundError as exc:
            click.echo(
                f"ghmem unroute: no issue or pull request numbered #{number}",
                err=True,
            )
            raise SystemExit(1) from exc
```

`GraphQLId` added to the `ghmem.aliases` import at `cli.py:8`. The
message now matches `show` (`cli.py:210`) and `discussion` (`:224`)
exactly, modulo the command name.

**FR-2 re-verified.** The loop still completes before the append loop
begins: `SystemExit` is raised from inside the resolution loop, so the
`for item_id in item_ids:` append loop below is unreachable on any
failure, and no partial batch can be written. Confirmed by test, not
by reading — `pytest tests/test_cli_unroute.py -k
rejects_an_unknown_number -q` → `1 passed, 5 deselected in 0.83s`.
That test asserts `(corpus / "routing.jsonl").read_text() == before`,
i.e. byte-identical.

`tests/test_cli_unroute.py:134` tightened from `assert "99" in output`
to `assert "ghmem unroute: no issue or pull request numbered #99" in
output`, and the docstring's `Then:` clause updated to name the shared
line rather than "an error naming #99".

### F3 — four test nits in `tests/test_unroute.py`

- `:50` — `UNROUTE_NEVER_ROUTED` renamed to `UNROUTE_3`, matching its
  `UNROUTE_1`/`UNROUTE_2` siblings. The two call sites (`:199`, `:251`)
  disagreed about whether I_3 was routed, so a name asserting either
  answer is wrong at one of them; the neutral name is correct at both,
  and each test's docstring already states which case it exercises.
- `:274` — `assert live[1]` added between the existing `live[0]` and
  `live[2]` guards, so all three shapes in the `live == rebuilt`
  comparison are individually proved non-empty.
- `:255-266` and `:268-278` — each of the six `select(...)` calls given
  a deterministic `order_by`: `Route.item_id` and `Dispatch.item_id`
  (matching `store.routes()` and `routed_items`), `Topic.name` (its
  primary key). ruff format reflowed the `Dispatch` lines.
- `:203-204` — **dropped** the vacuous `assert session.get(Route,
  GraphQLId("I_3")) is None`, along with the `with _session(...)` block
  that existed only to hold it. Reason for dropping rather than
  commenting: the assertion is not just non-discriminating today, the
  state it guards against is unrepresentable — `_apply_unroute_event`
  only ever deletes, so no mutation of that branch can *create* a
  `Route` for an item that never had one. A passing test for an
  unreachable state reads as verification and freezes dead machinery.
  The test's docstring `Then:` clause ("recording it raises nothing,
  and a rebuild from the resulting ledger raises nothing either")
  already describes exactly what remains, so nothing was lost.

### F4 — `Snapshot` rows in `_release_topic`'s docstring

**Verified first, as instructed.** `close_topic`
(`src/ghmem/dispatch_store.py:322-336`) reads the `Snapshot`, calls
`current = _topic_digest(session, snapshot.topic)` — recomputed live,
never compared against itself — and on `current != snapshot.digest`
returns `CloseOutcome(snapshot.topic, cleared=False)` before any
append or commit. `_topic_digest` (`:235-266`) hashes the ordered
`(Route.item_id, IssueOrPR.updated_at)` rows for `verdict == name`;
over an emptied topic that row set is empty, so it cannot equal a
digest taken over a non-empty set. The behaviour is as the review
described, so I documented it rather than reporting a discrepancy.

One paragraph added at `src/ghmem/routing_reduce.py:258-266`, after
the existing `docs/design/<name>.md` paragraph. No test added — the
behaviour is correct.

### F5 — C-1 in `plans/unroute-event/requirements.md`

**Reproduced before editing.** Commented out `| UnrouteEvent` from the
`RoutingEvent` union (`ledger.py:329`) and ran mypy:

```
tests/test_unroute.py:83: error: Argument 2 to "record_routing_event" of "Store" has incompatible type "UnrouteEvent"; expected "DispatchedEvent | RouteEvent | RequeueEvent | SnapshotEvent | CloseEvent | ReenterEvent"  [arg-type]
… (9 more identical, lines 101/129/151/181/202/226/250/251/306)
src/ghmem/cli.py:129: error: Type of TypedDict is ambiguous, none of ("DispatchedEvent", "RouteEvent", "RequeueEvent", "SnapshotEvent", "CloseEvent", "ReenterEvent") matches cleanly  [misc]
src/ghmem/cli.py:129: error: Incompatible types in assignment (expression has type "dict[str, Collection[str]]", variable has type "DispatchedEvent | RouteEvent | RequeueEvent | SnapshotEvent | CloseEvent | ReenterEvent")  [assignment]
```

12 errors — the two `cli.py:129` ones from the annotated event
literal, plus ten from the test module's own `UnrouteEvent`-annotated
constants. `ledger.py` restored from a backup taken after the F1 edit;
`git diff --stat` on it shows only the F1 hunk (4 insertions, 3
deletions).

C-1 (`requirements.md:148-166`) rewritten to state the surviving
constraint positively, in three parts: the instruction (add the union
member in the same change), the read path (still unchecked — the
`cast` in `Ledger.__iter__` launders replayed events), and the write
path (checked wherever a caller annotates its literal
`RoutingEvent`, with the exact mypy message and the residual gap: a
slice whose event kind is only ever replayed gets no warning). No
reference to the earlier draft.

## Not applied

- `ledger.py:86` and `ledger.py:99` — stale in the same
  `raw_nodes.py`-split way as the `models.py` hits, and `ledger.py` is
  in my ownership, but the brief classified them as correct and told
  me to leave them. Reported above with the evidence rather than
  fixed. One line each; trivial to repoint to `raw_nodes.py` if the
  lead wants it.
- `cli.py:128`'s `Unrouted {len(item_ids)} item(s)` count — a Minor in
  the review, explicitly noted there as conforming to FR-2 and pinned
  by a test. Not in my fix list; untouched.

## Verification

```
✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits
```

Run cold after the last edit (`just green` from
`/Users/david/code/micro/tools/ghmem`).

F2 no-append re-check: `pytest tests/test_cli_unroute.py -k
rejects_an_unknown_number -q` → `1 passed, 5 deselected in 0.83s`;
the test's `read_text() == before` assertion is what proves the ledger
is byte-identical after the failed run.

F5 mypy reproduction: quoted in full above; `ledger.py` restored and
verified to carry only the F1 hunk.

`git status --porcelain` from `/Users/david/code/micro`:

```
M  .claude/handoff-task.md
M  .claude/handoff-todo.md
 M docs/ghmem-changelog.md
 M plans/unroute-event/requirements.md
 M tools/ghmem/README.md
 M tools/ghmem/src/ghmem/cli.py
 M tools/ghmem/src/ghmem/exceptions.py
 M tools/ghmem/src/ghmem/ledger.py
 M tools/ghmem/src/ghmem/routing_reduce.py
 M tools/ghmem/src/ghmem/triage.py
 M tools/ghmem/tests/test_cli_unroute.py
 M tools/ghmem/tests/test_unroute.py
?? plans/ghmem-design-migration/reports/
?? plans/unroute-event/reports/
```

Every path I touched is on my ownership list.
`docs/ghmem-changelog.md` and `tools/ghmem/README.md` are the other
agents' — they were clean when I started and I did not open either.
The two staged `.claude/handoff-*.md` entries were already staged at
session start. Nothing committed; no branch created.

## Addendum: F6 — the four reclassified attribution refs

Follow-up dispatch after the lead confirmed the reclassification and
extended ownership to `src/ghmem/models.py` for this defect only. Each
repointed to the symbol's real home rather than having the module
reference deleted, since all four docstrings are routing tables.

- `src/ghmem/ledger.py:86` — "`ingest.py`'s `_RawNode` subclasses
  annotate `_event_type`" → "`raw_nodes.py`'s". The `_event_type`
  annotations are at `raw_nodes.py:69/114/148/180/212/237/261/290/321`
  and nowhere else.
- `src/ghmem/ledger.py:99` — "the shape of a raw node is `ingest.py`'s
  contract (its `_Raw*` structs)" → "`raw_nodes.py`'s contract".
- `src/ghmem/models.py:9-11` — "`ingest.py` owns turning raw GraphQL
  JSON into these entities and replaying them into SQLite" split
  across the two real owners: "`raw_nodes.py` owns turning raw GraphQL
  JSON into these entities and `ingest.py` replays them into SQLite".
  Both halves of the original sentence were true of different modules,
  so the fix is a split rather than a substitution.
- `src/ghmem/models.py:136` — "every raw-node parser in `ingest.py`" →
  "in `raw_nodes.py`".

**`models.py:96-97` read and judged: accurate as written, left
alone.** "a JSON column pulls its (de)serialization out of `ingest.py`
and `store.py` entirely" is a claim about entity-to-column
marshalling, not about where a symbol lives, and both named modules
are still the ones that would carry it. `raw_nodes.py:293-301`
(`_RawPRDiff.parse`) already produces `FileStat` objects, so it is not
a candidate: the work `_DiffStatType` absorbs is what happens between
that tuple and the `Text` column — the write side at `ingest.py:111`'s
`session.merge`, the read side in `store.py`'s queries. Without the
TypeDecorator both would need diffstat marshalling. Nothing else in
`models.py` was touched.

`src/` is now clean of misattributed `ingest` references: the only
remaining hits in these two files are `models.py:10` and `:97` (both
correct) and `ledger.py:25` ("bulk ingestion", the activity, not the
module).

### Verification

```
✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits
```

`git status --porcelain` adds `tools/ghmem/src/ghmem/models.py` to my
modified set and nothing else. The four new entries appearing since
the first run — `docs/ghmem-design.md`,
`plans/ghmem-design-migration/reduction-report.md`, `.../report.md`,
`docs/ghmem-changelog.md`, `tools/ghmem/README.md` — belong to the
other two agents; I did not open any of them. Nothing committed.

## Addendum 2: F7 — the last three `_Raw*` attributions, and the sweep

Ownership extended to `src/ghmem/sync_core.py` and `src/ghmem/store.py`
for this defect. All three fixed; `just green` re-run and green.

- `src/ghmem/sync_core.py:17` — "that is ingest.py's `_Raw*` family" →
  "that is raw_nodes.py's `_Raw*` family". The paragraph's tail was
  rewrapped to ~72 columns; wording unchanged apart from the module
  name.
- `src/ghmem/sync_core.py:154` — "keeps exactly the keys `ingest.py`'s
  matching `_Raw*` struct declares" → "`raw_nodes.py`'s matching".
- `src/ghmem/store.py:6-8` — split rather than repointed, since both
  halves were true of different modules: "The raw-node parsing and
  replay live in `ingest.py`" → "The raw-node parsing lives in
  `raw_nodes.py` and the replay driver in
  `ingest.rebuild_from_ledgers`". Paragraph rewrapped.

### Root cause

The drift predates this slice entirely. It comes from the earlier
split that moved the `_Raw*` structs out into `raw_nodes.py`, which
repointed the imports and left every prose reference behind — the same
failure mode the `routing_reduce.py` split then repeated. The
deliverable review scoped its M2 finding to the `routing_reduce.py`
seam, which is why it reported six references and the true count was
thirteen: the six from this slice (F1), four from the `raw_nodes.py`
split found in the first sweep (F6), and three more here (F7). Any
future module split should end with a `grep -rn "<old-module>" src/`
over prose, not just a check that the imports resolve.

### Exit sweep

`grep -rn "ingest" src/ghmem/*.py`, minus `ingest.py` itself — all
sixteen remaining hits classified, none stale:

| hit | classification |
| --- | --- |
| `heat_query.py:8` | accurate — `store.py:52` really does import `rebuild_from_ledgers` from `ghmem.ingest` |
| `ledger.py:25` | not a module reference — "bulk ingestion", the activity |
| `models.py:10` | accurate as fixed in F6 — `ingest.py` owns the replay |
| `models.py:97` | accurate — judged in F6; a claim about entity-to-column marshalling, not symbol location |
| `raw_nodes.py:44` | accurate — `ingest.py:45` imports `_RAW_BY_TYPE` from here |
| `raw_nodes_core.py:6` | accurate — `validate_raw_node` is defined at `ingest.py:73` |
| `routing_reduce.py:4` | accurate — `rebuild_from_ledgers` is at `ingest.py:49` |
| `routing_reduce.py:9` | accurate — a historical statement about where this module was split from |
| `status.py:3` | accurate — `ingest.py` is the replay driver |
| `store.py:7` | accurate as fixed above |
| `store.py:52` | an import statement |
| `store.py:106` | accurate — `ingest.rebuild_from_ledgers` |
| `sync_core.py:10` | accurate, borderline — see below |
| `sync_core.py:19` | accurate — `ingest.validate_raw_node` is real |
| `sync_core.py:33` | an import statement |
| `sync_core.py:144` | accurate, borderline — see below |

**The two borderline hits, left alone, with the reasoning so the call
is cheap to overrule.** `sync_core.py:10` ("turns a wire node into
ingest's raw `data` contract") and `sync_core.py:144` ("wire Struct ->
ingest.py's `data` contract") are structurally similar to the two
`sync_core.py` references I just fixed: the `data` payload's *shape*
is declared by the `_Raw*` structs, which live in `raw_nodes.py`. What
makes them read differently is that neither names a symbol — both name
a contract by the module that enforces and consumes it, and that is
`ingest.py`: `validate_raw_node` (`ingest.py:73`) is the gate every
projected node passes through, and `rebuild_from_ledgers` is what
consumes the result. The fixed pair named the `_Raw*` family itself,
which is a symbol location and was wrong. I read these as accurate and
did not change them, matching the lead's own check.

### Verification

```
✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits
```

`awk 'length > 79'` over both touched files reports only pre-existing
code lines (imports, `select()` chains, f-strings); no prose line I
wrote or rewrapped exceeds ~72 columns. My modified set now adds
`tools/ghmem/src/ghmem/sync_core.py` and
`tools/ghmem/src/ghmem/store.py`. Nothing committed.
