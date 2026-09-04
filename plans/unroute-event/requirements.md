# Unroute Event

Return an already-routed ghmem corpus item to the triage queue so a
fresh worker can re-judge it, without rewriting the append-only routing
ledger.

## Requirements

### Functional Requirements

**FR-1: An `unroute` routing event that clears both `Route` and
`Dispatch`**

A new member of the `RoutingEvent` union carrying one field, the
item's `item_id`. Its reducer branch in `apply_routing_event` deletes
the item's `Route` row and its `Dispatch` row.

Deleting the `Route` is what distinguishes this from the existing
`reenter` event, which deletes only the `Dispatch`. Three independent
freshness gates each exclude an item carrying a fresh `Route`, and all
three must fall for a re-triage to complete:

- `heat_query.untriaged_heat_scores` excludes an item whose
  `Route.item_updated_at >= item.updated_at`, so the item is never
  served;
- `dispatch_store.stale_routes` selects only routes the item has moved
  past, so `rebuild` never reenters it;
- `triage.record_verdict` raises `already_routed` on a fresh `Route`,
  so even a served item could not be re-routed.

With no `Route` row at all, each gate passes on the absence rather than
on a relaxed comparison. No existing guard is weakened.

Acceptance criteria:
- Applying the event to a mirror holding a fresh `Route` and a
  `Dispatch` for the item leaves neither row.
- Applying it to an item with no `Route` and no `Dispatch` is a no-op,
  not an error, so replay stays idempotent — matching how the
  `requeue` branch already treats an absent `Dispatch`.
- After the event, `untriaged_heat_scores` returns the item and
  `record_verdict` accepts a verdict for it under a fresh session.

**FR-2: A `ghmem unroute` command**

Appends one `unroute` event per named item through
`Store.record_routing_event`, so the ledger append and the mirror
write follow the same interleaved ordering every other mutating
command uses. Prints the count.

Acceptance criteria:
- Names items by issue/PR **number**, matching `show <number>`,
  `triage` and `route`.
- An unknown number is rejected before any event is appended.
- Running it twice over the same items appends events the second time
  that reduce to no change, and a subsequent `rebuild` agrees with the
  live mirror.

**FR-3: The vacated topic is dirtied, or dropped if left empty**

An item leaving a real (non-`NO_SIGNAL`) verdict dirties that topic, so
the topic doc gets rewritten without the item — as `route`'s
leaving-topic step and `_apply_reenter_event` already do. When the
leaving item was the topic's last, the `Topic` row is deleted instead
(Q-2). `route` is unchanged.

**FR-4: Emptying a topic must not break synthesis**

Unrouting the last item of a topic leaves a `Topic` row with
`dirty=True` and zero routed items. This is the **dominant** case for
the batch this event exists to serve, not an edge case: 57 of the
corpus's 71 topics hold exactly one item, and 20 of the 22 items to be
re-triaged minted a fresh name.

Re-routing the last item out of a topic reaches the same state today,
so the path is not new — but no corpus route has ever done it, and it
is not handled:

- `status` lists the empty topic under both `topics:` and `dirty:`;
- `topic.assemble` raises `TopicNotRoutedError` for it, so a synthesis
  worker dispatched from the dirty list fails hard on an emptied name.

The event must leave the corpus in a state where the normal
`status` → `topic` → synthesize loop runs to completion. Which
mechanism achieves that is Q-2.

Acceptance criteria:
- After unrouting every item of a topic, dispatching the documented
  synthesis loop over `status`'s dirty set completes without raising.

**FR-5: Replay and live write agree**

Replaying a ledger containing `unroute` events through
`rebuild_from_ledgers` produces the same `Route`, `Dispatch` and
`Topic` rows as applying them live. This is the architecture's central
claim and `test_routing_live.py` already pins it for the other event
kinds.

Acceptance criteria:
- A live unroute followed by a cold rebuild leaves observable rows
  unchanged, asserted over a fixture where both sides are non-empty
  (an all-empty comparison is vacuously true).

**FR-6: The batch this is built for is re-triaged**

The 22 items routed by sessions `BcbBJeUg` and `MZlH9vbE` are
unrouted, then re-triaged by `ghmem-triage` workers that now receive
the topic list in their packets.

Acceptance criteria:
- All 22 carry a `Route` written by a session other than those two.
- The new verdicts are drawn from the served topic list where a
  near-duplicate exists, and the corpus's singleton-topic count falls.

### Non-Functional Requirements

**NFR-1: The routing ledger stays append-only**

No line of `routing.jsonl` is edited or deleted. Correction happens by
appending an event that supersedes, exactly as the #1445 re-route and
the five `no-signal` backfills did. `routing.jsonl` is the one corpus
file a re-sync cannot regenerate.

**NFR-2: Every touched source file stays under 400 lines**

`scripts/check_line_limits.sh` runs inside `just green`. Sizes before
this change of the files it touches: `ingest.py` 339, `cli.py` 313,
`ledger.py` 308, `dispatch_store.py` 355. None has room to spare for a
large addition; a split is a live possibility rather than a formality.

It was needed. `ingest.py` reached 416 lines, so the routing reducer
moved to `routing_reduce.py` along the seam `ingest.py`'s own module
docstring already draws — that module owns the fetch ledger's raw
nodes and the drop-and-replay driver, the new one owns what a routing
event means. `ingest.py` is 111 and `routing_reduce.py` 328.
`dispatch_store.py` and `ingest.py` now import `apply_routing_event`
from the new module directly; `store.py` and `sync_core.py` still
reach `rebuild_from_ledgers` and `validate_raw_node` in `ingest.py`,
which kept them. No compatibility alias was left behind.

**NFR-3: `just precommit` passes**

The repo gate — ruff, docformatter `-c`, mypy strict, pytest, the bats
hook suites, and the line-limit check — runs green before the work is
called done.

### Constraints

**C-1: `ledger.py` must be edited in the same change**

An event kind added to the reducer but not to the `RoutingEvent`
TypedDict union is runtime-complete and type-incomplete. Add the
member to the union in the same change as the reducer branch.

What the gate sees depends on the call shape. The **read** path is
unchecked: `Ledger.__iter__` launders every line through a `cast`, so
a replayed event of a kind the union does not name type-checks
regardless. This cost an Important finding once already, on the
discussions slice.

The **write** path is checked wherever a caller annotates its event
literal `RoutingEvent` — `cli.py`'s `event: RoutingEvent` is the live
example. Omit the union member and mypy fails on the assignment
(`Type of TypedDict is ambiguous, none of (…) matches cleanly`), so
`just green` goes red without pytest noticing anything. A slice whose
new event kind is only ever replayed, never written through such an
annotated literal, gets no such warning.

**C-2: The event must also be registered in `_ROUTING_BY_TYPE`**

That map is what `validate_routing_event` checks a ledger line
against. A tag with a reducer branch but no entry there is rejected
before reaching the branch.

**C-3: TDD red means a failed assertion, not a missing symbol**

Stub the new event handling importable but inert, run the batch, and
confirm each test fails on its own assertion before implementing.

Two shapes are specifically at risk here and neither is hypothetical
— both are recorded ghmem defects:

- an idempotence test ("append twice, rebuild, same rows") stays green
  with the `Route` delete removed, because every other write is an
  upsert. Only a row that must **vanish** proves a deletion runs.
- `topic.dirty is True` after an unroute is satisfied by birth state,
  since a `Topic` row is created dirty and nothing clears it. Clean the
  topic first, then assert the transition.

**C-4: Mutation-test the reducer branch**

Every task of ghmem dispatch B1 shipped at least one test that passed
for the wrong reason, and reading the tests found none of them. The
mutation-script sweep is what has caught these each time.

**C-5: Re-verify the containment hook against a real command**

`hooks/scripts/ghmem-only.sh` is a positive character allowlist over
the whole command string, not a list of subcommands, so `ghmem
unroute 123` is permitted by construction. Confirm that by running the
real string through the hook's own predicate expecting ALLOW, paired
with a must-block case expecting DENY — a guard broken closed looks
exactly like success from the deny alone.

**C-6: Docstring summaries stay at or under 80 total characters**

docformatter's `wrap-summaries=80` against ruff's line-length 88 is a
dead zone: an 81–88 character summary passes ruff on one line, gets
reflowed to two, and then trips ruff D205. `just green` has no fixed
point on such a docstring.

### Out of Scope

- **The `topic merge` event** — mapping a topic name to a canonical
  one, replayed by `rebuild`. It addresses the same singleton problem
  from the other side and remains the next lever, but it is a separate
  event with its own semantics and it cannot re-judge the 12 of the 22
  items that minted names outside the named near-duplicate clusters.
- **The delta form of the packet topic list** — deferred while the
  full list costs 1802 characters against a 200,000-character
  `BATCH_CHARS`, and revisited only if the topic list grows rather
  than shrinking.
- **Reverting the triage rubric revision** — the no-signal collapse
  (4 of 95 routes against a 36.5% calibrated base rate) is a real open
  question, but it is a judgement about prompt content, independent of
  whether a routed item can return to the queue.
- **Relaxing `record_verdict` to accept a live re-route** — the
  freshness check is what distinguishes a live re-route from a
  reentered item's re-triage. Clearing the `Route` reaches the goal
  without touching that distinction.
- **Re-triaging routes outside the named 22** — the other 73 routes
  were judged with whatever the packet carried at the time and are not
  in question here.
- **Deleting or rewriting ledger history** — see NFR-1.

### Dependencies

- **The packet topic list must already be served.** It is: commit
  `92d1283`, "ghmem: carry the topic list in every triage packet".
  Without it the re-triage reproduces the original defect exactly, and
  the workers would mint blind a second time.
- **`ghmem-triage` workers must be dispatched by `subagent_type` with
  no custom `name`.** A name overwrites `agentType` with
  `customAgentType`, the frontmatter hooks register nothing, and the
  worker runs with an unrestricted shell. This is how the corpus was
  corrupted once already.

### Decisions

**Q-1 — positional issue/PR numbers, variadic, at least one required.**

`ghmem unroute NUMBER...` names items the way `show`, `triage` and
`route` do. The batch's session identity is a property of *this* job,
not of the command: the 22 numbers are derivable once with a query
over `Route.session`, and the command that consumes them stays in
ghmem's one vocabulary. A `--session` selector remains additive if a
second caller ever appears; removing one would not be. The allowlist
hook's argument class permits either form, so containment does not
decide it.

**Q-2 — the reducer deletes a `Topic` row the unroute leaves empty.**

A `Topic` row exists exactly while some `Route` carries its name. The
reducer's leaving-topic step becomes: dirty the topic if routed items
remain, delete its row if none do. Nothing else changes — `status`
stops listing it under `topics:` and `dirty:` because it is gone, and
`topic.assemble` is never dispatched for it, so FR-4's loop runs to
completion with no special case anywhere downstream.

Two consequences are deliberate:

- **The step is scoped to `unroute`, and `route` keeps its behaviour.**
  Re-routing the last item out of a topic reaches the same empty state,
  and three tests in `test_routing_replay.py` plus two in
  `test_routing_live.py` pin the current answer to it — the emptied
  topic survives as a dirty row. There is a real distinction to stand
  on: a re-routed item is still in the corpus under another name, so
  the emptied topic's doc has a rewrite to receive, while a withdrawn
  verdict leaves the name standing for nothing. Whether the two paths
  should converge is a live question and an explicitly tested contract
  to overturn, so it is left open rather than settled inside this
  change. No corpus route has ever emptied a topic: the mirror holds
  zero empty `Topic` rows across 95 routes.
- **Nothing removes `docs/design/<name>.md`.** The reducer writes
  SQLite and is replayed by `rebuild`; a filesystem delete inside it
  would make replay destructive. A synthesized doc is prose about items
  that were really routed there, so an emptied-but-synthesized topic
  keeps its doc, and `ghmem index` keeps listing it from disk. For this
  batch the question is moot: none of the 20 topics the 22 items empty
  has a doc.

**Q-3 — no containment change; `ghmem unroute` stays permitted.**

The escalation the question names does not exist. `unroute` clears the
`Dispatch` along with the `Route`, so a worker that unroutes an item it
was rejected on immediately fails `record_verdict`'s `not_dispatched`
guard. Its only route back is to be served the item again through the
metered queue, which is the sanctioned loop.

The residual — a confused worker returning other sessions' items to the
queue — is not a new class of exposure. `ghmem-only.sh` is a
command-*shape* allowlist, and it already permits `ghmem requeue` and
`ghmem rebuild`, both corpus-wide mutating operator commands. Singling
out `unroute` there would be theatre while those stay open, and "which
subcommands may this caller run" needs authority vocabulary the hook
does not have — it sees one command string, and one copy of it contains
both agents. No incident has occurred; the rule waits for one.

**Q-4 — uniform: `unroute` treats every verdict alike, `NO_SIGNAL`
included.**

The `Route` row is deleted whatever it holds; the topic-release step is
skipped for `NO_SIGNAL` exactly as `route`'s leaving branch and
`_apply_reenter_event` already skip it. Refusing `NO_SIGNAL` would make
it the one verdict that can never be corrected, and with `no-signal`
running at 4 of 95 routes against a 36.5% calibrated base rate, that is
the population most likely to need correcting.

### References

- `plans/unroute-event/recall-artifact.md` — curated memory entries
- `docs/ghmem-design.md` — ghmem spec,
  sole source of truth for the pipeline's decisions
- `.claude/handoff-task.md` — the re-triage task this event serves
- Corpus measurements taken 2026-09-02 against
  `corpus/cache.sqlite`: 95 routes, 71 topics, 57 singletons, 4
  `no-signal`, 0 items reachable by `requeue` or `reenter`
