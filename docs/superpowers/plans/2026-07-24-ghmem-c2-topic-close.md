# ghmem C2 (`topic` + `close`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan
> task-by-task. Two tasks, Sonnet implementer / Opus reviewer per
> [[sdd-reviewer-tiering]].

**Goal:** Add `ghmem topic <name>` (assemble a topic's synthesis
context behind a snapshot token) and `ghmem close <token>` (record
synthesis completion with a live-decided compare-and-set).

**Architecture:** `topic.py` holds the library logic, mirroring
`triage.py`; `close`'s compare-and-set transaction lives in
`dispatch_store.py` beside `reserve_dispatch`; the `Store` facade grows
three delegating methods. Two new routing-ledger members
(`SnapshotEvent`, `CloseEvent`) and one new table (`Snapshot`).

**Tech stack:** Python 3.14, SQLAlchemy 2.0 typed declarative, msgspec,
click, pytest.

This is a **slim** plan: it pins interfaces, the two tricky algorithms
(digest serialization; the `close_topic` transaction), and the per-task
test/mutation targets. It deliberately omits per-step "write test / run
/ commit" scaffolding — the executor follows the standard TDD cycle
(failing test → run red → minimal impl → run green → commit) for each
listed test. Source is written in full at execution time, not here
([[plan-contracts-not-full-code]]).

The source of truth is the spec:
`docs/superpowers/specs/2026-07-24-ghmem-c2-topic-close-design.md`. Do
not re-derive its decisions; this plan settles only its three
plan-time open questions (task seam, digest-helper placement,
item-index line format).

## Global constraints

- **Gate:** `just green` from `tools/ghmem` (ruff ALL, docformatter,
  mypy strict, pytest, 400-line/file cap). Must pass at each task's end.
- **Digest is single-homed:** exactly one function defines the content
  digest; both `topic_digest` and `close_topic` call it. Never inline a
  second digest computation.
- **Write only current outcomes to the ledger:** a `CloseEvent` is
  appended only on a clean close. The stale path writes nothing. This
  is load-bearing, not cosmetic (a stale `CloseEvent` would clear the
  topic on replay *after* the route that staled it, dropping input).
- **`ledger.py` grows in both tasks** — the `RoutingEvent` union and
  its `_ROUTING_BY_TYPE` map are edited alongside the `ingest.py`
  reducer every time. A task that touches the reducer but not the union
  is incomplete.

## File structure

- `src/ghmem/ledger.py` — `SnapshotEvent`/`SnapshotData` (T1),
  `CloseEvent`/`CloseData` (T2) added to the `RoutingEvent` union and
  `_ROUTING_BY_TYPE`.
- `src/ghmem/models.py` — new `Snapshot` table (T1).
- `src/ghmem/ingest.py` — `apply_routing_event` grows a `"snapshot"`
  branch (T1) and a `"close"` branch (T2).
- `src/ghmem/dispatch_store.py` — `_topic_digest`, `topic_digest`,
  `routed_items` (T1); `CloseOutcome`, `close_topic` (T2).
- `src/ghmem/store.py` — facade methods `routed_items`, `topic_digest`
  (T1), `close_topic` (T2).
- `src/ghmem/topic.py` — NEW. `assemble` and the item-index / thread
  rendering; snapshot-token minting (T1).
- `src/ghmem/exceptions.py` — `TopicNotRoutedError` (T1),
  `UnknownSnapshotError` (T2).
- `src/ghmem/cli.py` — `topic` command (T1), `close` command (T2).
- `tests/test_topic.py` — library tests for `assemble` (T1) and
  `close_topic` (T2).
- `tests/test_cli_topic.py` — CLI smoke test (T1).
- `tests/test_cli_close.py` — CLI end-to-end test (T2).
- `tests/test_routing_live.py` — the off-union fixture is retagged (T2).

Test fixtures reuse existing corpora — no new fixture file:
`heat_corpus.jsonl` gives ≥2 items with **distinct heat and distinct
`updated_at`** (the "too-uniform-to-bite" trap the spec flags);
`show_corpus.jsonl` gives PR #2 with a `pr_diff` for the diffstat line.
Routing is driven live in each test (record `dispatched`+`route`
events), as `test_routing_live.py` already does.

---

## Task 1: `ghmem topic` — assembly + snapshot

**Files:** Create `src/ghmem/topic.py`, `tests/test_topic.py`,
`tests/test_cli_topic.py`; modify `ledger.py`, `models.py`, `ingest.py`,
`dispatch_store.py`, `store.py`, `exceptions.py`, `cli.py`.

**Interfaces produced:**

```python
# ledger.py — join the RoutingEvent union + _ROUTING_BY_TYPE["snapshot"]
class SnapshotData(TypedDict):
    token: str
    topic: str
    digest: str
class SnapshotEvent(TypedDict):
    type: Literal["snapshot"]
    data: SnapshotData
# RoutingEvent docstring note becomes "Grows in Part C (CloseEvent)".

# models.py
class Snapshot(Base):
    token: Mapped[str] = mapped_column(primary_key=True)
    topic: Mapped[str]
    digest: Mapped[str]

# dispatch_store.py
def _topic_digest(session: Session, name: str) -> str: ...
def topic_digest(engine: Engine, name: str) -> str: ...          # opens a session, calls _topic_digest
def routed_items(engine: Engine, name: str) -> list[tuple[IssueOrPR, Route]]: ...

# store.py facade
def routed_items(self, name: str) -> list[tuple[IssueOrPR, Route]]: ...
def topic_digest(self, name: str) -> str: ...

# exceptions.py
class TopicNotRoutedError(ValueError):   # carries .name, message f"topic {name!r} has no routed items"

# topic.py
def assemble(store: Store, routing: Ledger[RoutingEvent], name: str) -> str: ...
```

**Tricky algo — the digest (the single definition):**

```python
def _topic_digest(session: Session, name: str) -> str:
    rows = session.execute(
        select(Route.item_id, IssueOrPR.updated_at)
        .join(IssueOrPR, IssueOrPR.id == Route.item_id)   # corpus constraint + updated_at
        .where(Route.verdict == name)
        .order_by(Route.item_id)                          # canonical order
    ).all()
    payload = "\n".join(f"{item_id}\t{updated_at.isoformat()}" for item_id, updated_at in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The `join` is doing two jobs: it drops routes to items not in the
corpus (matching `routes()`), and it supplies each item's `updated_at`.
`routed_items` uses the same `verdict == name` + join predicate so its
membership agrees with the digest's.

**`ingest.py` — `"snapshot"` reducer branch** (a plain upsert, touches
no dirty flag; place it before the closing `raise`):

```python
if event["type"] == "snapshot":
    data = event["data"]
    session.merge(Snapshot(token=data["token"], topic=data["topic"], digest=data["digest"]))
    return
```

**`topic.py` — `assemble` shape** (bodies written at execution time):

1. `routed = store.routed_items(name)`; if empty, `raise TopicNotRoutedError(name)`.
2. `scores = store.heat_scores_for(HEAT_WEIGHTS, [item.number for item, _ in routed])` — heat order, hottest first; `title` and the four counts come from each `HeatScore`.
3. `digest = store.topic_digest(name)`; mint an 8-char base62 token
   (`secrets.choice` over `string.digits + string.ascii_letters`, its
   own constants in `topic.py` — a distinct namespace from session
   tokens that merely shares the shape, so it does not import
   `triage`); record `SnapshotEvent(type="snapshot", data={token, topic:name, digest})` via `store.record_routing_event`.
4. Render: a `snapshot: <token>` header, a `## Item index` section (one
   line per routed item, in `scores` order), then a `## Threads` section
   of `show.render(store, standing, number)` per item at the default
   diff valve.

**Item-index line format** (settled — the spec fixed the field set, not
the punctuation). Per item, using its `HeatScore` (`score`), its
`IssueOrPR` (`item`), and its `Route.note`:

```
- #{number} — {title} — {state_lower}, opened {created:YYYY-MM-DD}, last active {updated:YYYY-MM-DD}, by {standing} · {c} comments · {r} reactions · {p} participants · {rel} related[ · {files} files +{adds}/-{dels}] · note: {note}
```

- `title` and `c/r/p/rel` come from `HeatScore` (`comments`,
  `reactions`, `participants`, `related_items`); `state_lower` is
  `item.state.lower()`; dates are `.date().isoformat()`; `standing` is
  `standing.annotation(item.author, item.created_at.date())`
  (created-date standing, matching `show._render_header`).
- The diffstat segment appears **only for a PR that has a fetched
  diff**: attempt `store.pr_diff_for(number)`, and on
  `PRDiffNotFoundError` omit the segment (an issue, or a PR whose diff
  never fetched, simply has no segment). `files = len(diff.diffstat)`,
  `adds`/`dels` sum `FileStat.additions`/`.deletions`.
- `note` is always present: `routed_items` only returns real-topic
  verdicts, and `record_verdict` requires a note for those.

**`cli.py` — `topic`:** open store + routing ledger, `assemble`, print
with `nl=False`; map `TopicNotRoutedError` to `ghmem topic: {exc}` on
stderr + exit 1 (the `IssueOrPRNotFoundError` pattern in `show`).

**Tests — `tests/test_topic.py`** (route two `heat_corpus` issues to
`"softwrap"` live, unless noted):

- `test_assemble_header_carries_the_minted_token` — output's first line
  is `snapshot: <tok>` with an 8-char base62 token; the ledger's last
  event is a `SnapshotEvent` carrying that token and topic `softwrap`.
- `test_assemble_item_index_is_in_heat_order` — the `#N` lines in the
  item index appear in the order `heat_scores_for(HEAT_WEIGHTS, routed
  numbers)` gives (computed independently in the test, the
  `triage_helpers._ranked_numbers` pattern), and the two routed items
  have **different** heat so the order is not a tie.
- `test_assemble_item_line_carries_note_and_counts` — a routed item's
  line contains `note: {its note}` and its `HeatScore` counts.
  **[mutation anchor: item index omits the routing note.]**
- `test_assemble_threads_carry_each_show_render` — for each routed item,
  `show.render(store, build_standing(store), number)` text is a
  substring of the `## Threads` section.
- `test_assemble_diffstat_for_a_pr` — over `show_corpus`, route PR #2
  (which owns the fixture's `pr_diff`); its line carries `1 files
  +10/-2`.
- `test_assemble_snapshot_digest_matches_recomputation` — the
  `SnapshotEvent`'s digest equals an **independently computed** sha256
  over sorted `(item_id, updated_at.isoformat())` of the routed items.
  **[mutation anchors: digest drops `updated_at`; digest drops
  membership — either makes the independent recomputation disagree.]**
- `test_assemble_empty_topic_raises` — `assemble(store, routing,
  "nonexistent")` raises `TopicNotRoutedError`, and nothing was
  appended to the ledger.

**Test — `tests/test_cli_topic.py`** (fixture pattern of
`test_cli_index.py`: `$GHMEM_CORPUS_DIR` → a corpus with `heat_corpus`
as `fetch.jsonl` and an inline `routing.jsonl` routing `I_1`+`I_2` to
`softwrap`, then `rebuild`):

- `test_topic_prints_context_and_exits_zero` — `ghmem topic softwrap`
  exits 0, stdout opens with `snapshot: ` and contains both `#`-lines.
- `test_topic_unrouted_name_exits_one` — `ghmem topic bogus` exits 1
  with the not-routed message on stderr.

**Task 1 done when** `just green` passes and the mutation anchors above
each kill their named test.

---

## Task 2: `ghmem close` — compare-and-set + INDEX regen

**Files:** Modify `ledger.py`, `ingest.py`, `dispatch_store.py`,
`store.py`, `exceptions.py`, `cli.py`, `topic.py` (nothing — `close`
goes through the facade), `tests/test_topic.py`,
`tests/test_routing_live.py`; create `tests/test_cli_close.py`.

**Interfaces produced:**

```python
# ledger.py — join the union + _ROUTING_BY_TYPE["close"]; remove the
# "Grows in Part C" docstring note (both members now present).
class CloseData(TypedDict):     # NO `cleared` field — event existence = clean
    token: str
    topic: str
class CloseEvent(TypedDict):
    type: Literal["close"]
    data: CloseData

# dispatch_store.py
@dataclass(frozen=True, slots=True)
class CloseOutcome:
    topic: str
    cleared: bool
def close_topic(engine: Engine, routing: Ledger[RoutingEvent], token: str) -> CloseOutcome: ...

# store.py facade
def close_topic(self, routing: Ledger[RoutingEvent], token: str) -> CloseOutcome: ...

# exceptions.py
class UnknownSnapshotError(ValueError):   # carries .token, message f"no snapshot for token {token!r}"
```

**Tricky algo — `close_topic` (the compare-and-set transaction):**

```python
def close_topic(engine, routing, token) -> CloseOutcome:
    with Session(engine) as session:
        snapshot = session.get(Snapshot, token)
        if snapshot is None:
            raise UnknownSnapshotError(token)
        current = _topic_digest(session, snapshot.topic)   # NOT snapshot.digest — compare against live
        if current != snapshot.digest:
            return CloseOutcome(snapshot.topic, cleared=False)   # stale: write NOTHING, no commit
        event: CloseEvent = {"type": "close", "data": {"token": token, "topic": snapshot.topic}}
        apply_routing_event(session, event)   # validates, then clears the topic's dirty flag
        routing.append(event)                 # ledger before the mirror commits (durable-truth-first)
        session.commit()
        return CloseOutcome(snapshot.topic, cleared=True)
```

The read, the decision, the ledger append and the mirror commit share
one `Session` — the compare-and-set sibling of `reserve_dispatch`. A
single-Session read-decide-write suffices (no IMMEDIATE txn): per the
spec's concurrency section, `close` never races `route` and distinct
synthesis agents close distinct topics.

**`ingest.py` — `"close"` reducer branch** (clears unconditionally on
the event's presence — no currency recheck; a missing `Topic` is a
no-op):

```python
if event["type"] == "close":
    data = event["data"]
    topic = session.get(Topic, data["topic"])
    if topic is not None:
        topic.dirty = False
    return
```

**`cli.py` — `close`:** open store + routing ledger,
`store.close_topic(routing, token)`; map `UnknownSnapshotError` to
`ghmem close: {exc}` on stderr + exit 1. On `cleared`, regenerate
`INDEX.md` exactly as the `index` command does — `(design_dir() /
"INDEX.md").write_text(render_index(build_index(store, design_dir())))`
— then `echo(f"closed {outcome.topic}")`. Otherwise
`echo(f"stale: {outcome.topic} changed since its snapshot; re-run ghmem
topic", err=True)` and exit 0.

**`test_routing_live.py` retag:** its
`test_a_rejected_event_never_reaches_the_ledger` uses `{"type":
"close", ...}` as its off-union example. `"close"` is now a real tag, so
the shape it casts (`{session, item_id}`) would raise
`msgspec.ValidationError`, not `UnknownRoutingEventError`. Retag the
fixture to an unmistakably-unknown type (e.g. `"bogus"`) so the test
still exercises the unknown-tag path.

**Tests — `tests/test_topic.py`** (extend; route two `heat_corpus`
issues to `softwrap`, `assemble` to mint a token, unless noted):

- `test_close_clean_clears_dirty_and_appends_one_close_event` — a clean
  `close_topic` returns `CloseOutcome("softwrap", cleared=True)`, the
  `Topic` row is `dirty=False`, and exactly one `CloseEvent` is appended
  to the routing ledger.
- `test_close_stale_after_fresh_route_writes_nothing` — after `assemble`
  mints the token, route a **third** item to `softwrap`, then
  `close_topic`: returns `cleared=False`, the topic stays `dirty=True`,
  and no `CloseEvent` was appended.
  **[mutation anchors: digest drops membership; `close_topic` appends on
  the stale path; `close_topic` compares the current digest against
  itself.]**
- `test_close_stale_after_activity_writes_nothing` — after minting,
  bump a routed item's `updated_at` in the mirror (a session `UPDATE`,
  standing in for a re-fetch), then `close_topic`: `cleared=False`,
  still dirty, nothing appended.
  **[mutation anchor: digest drops `updated_at`.]**
- `test_close_unknown_token_raises` — `close_topic(engine, routing,
  "nope")` raises `UnknownSnapshotError`.
- `test_close_clean_matches_a_cold_rebuild` — after a clean close,
  rebuild the store from the ledger the close wrote; the `Topic` is
  `dirty=False` both live and rebuilt (extends
  `test_routing_live.test_live_writes_match_a_cold_rebuild`).
  **[mutation anchor: reducer close branch no-ops — replay would leave
  the topic dirty.]**

**Test — `tests/test_cli_close.py`** (corpus fixture as in
`test_cli_topic.py`, routing `I_1`+`I_2` to `softwrap`):

- `test_close_clean_regenerates_index_and_recaps` — invoke `topic
  softwrap`, parse `snapshot: <tok>` from stdout, invoke `close <tok>`:
  exit 0, stdout `closed softwrap`, and `INDEX.md` exists in the design
  dir (`$GHMEM_DESIGN_DIR`) with `softwrap` no longer marked pending.
- `test_close_unknown_token_exits_one` — `ghmem close deadbeef` exits 1
  with the no-snapshot message on stderr.

**Task 2 done when** `just green` passes and every mutation anchor above
kills its named test.

---

## Self-review

**Spec coverage** — `topic` assembly + `SnapshotEvent` + `Snapshot`
(T1); `close` three outcomes (clean/stale/unknown) + `CloseEvent` +
INDEX regen (T2); digest over sorted `(item_id, updated_at)` single-homed
(`_topic_digest`, T1); reducer `snapshot`/`close` branches (T1/T2);
live-vs-rebuild property (`test_close_clean_matches_a_cold_rebuild`);
item index field set + heat order + threads (T1). All spec sections map
to a task. The two honest limits (reaction not always bumping
`updated_at`; notes out of the digest) are recorded design limitations,
not code — no task.

**Mutation targets** — all six from the spec are placed:
updated_at-drop and membership-drop (T1 digest test + T2 stale tests),
stale-path-appends (T2 `test_close_stale_after_fresh_route`), reducer
close no-op (T2 rebuild test), item-index-omits-note (T1), compare
current-against-itself (T2 stale tests).

**Type consistency** — `CloseOutcome(topic, cleared)` returned by
`close_topic` and read by `cli.close`; `_topic_digest(session, name)`
called by both `topic_digest` and `close_topic`; `SnapshotData` fields
`{token, topic, digest}` and `CloseData` `{token, topic}` match the
`Snapshot` columns and the reducer branches. `routed_items` returns
`list[tuple[IssueOrPR, Route]]` consumed by `assemble`.
