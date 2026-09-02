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

**FR-3: The vacated topic is marked dirty**

An item leaving a real (non-`NO_SIGNAL`) verdict dirties that topic, so
the topic doc gets rewritten without the item. This mirrors what the
`route` branch already does for the topic an item leaves on a
re-route, and what `_apply_reenter_event` does.

**FR-4: Emptying a topic must not break synthesis**

Unrouting the last item of a topic leaves a `Topic` row with
`dirty=True` and zero routed items. This is the **dominant** case for
the batch this event exists to serve, not an edge case: 57 of the
corpus's 71 topics hold exactly one item, and 20 of the 22 items to be
re-triaged minted a fresh name.

Today that state is reachable by no other path, and it is not handled:

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

`scripts/check_line_limits.sh` runs inside `just green`. Current sizes
of the files this touches: `ingest.py` 339, `cli.py` 313, `ledger.py`
308, `dispatch_store.py` 355. None has room to spare for a large
addition; a split is a live possibility rather than a formality.

**NFR-3: `just precommit` passes**

The repo gate — ruff, docformatter `-c`, mypy strict, pytest, the bats
hook suites, and the line-limit check — runs green before the work is
called done.

### Constraints

**C-1: `ledger.py` must be edited in the same change**

An event kind added to the reducer but not to the `RoutingEvent`
TypedDict union is runtime-complete and type-incomplete, and
`just green` cannot see the gap: the read path launders through a
`cast` in `Ledger.__iter__`. This cost an Important finding once
already, on the discussions slice.

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

### Open Questions

- **Q-1: What selects the items to unroute?** Positional numbers match
  every other ghmem command, but the batch is naturally identified by
  the two session tokens that produced it. A `--session` selector would
  fit this job and may have no second caller. Positional numbers are
  the conservative default.
- **Q-2: What happens to a topic whose last item leaves?** Delete the
  `Topic` row in the reducer; leave it and make `status` omit empty
  topics from the dirty set; or leave it and make `topic.assemble`
  return a deletion instruction rather than raise. The choice
  determines whether a synthesized `docs/design/<name>.md` for an
  emptied topic is removed, and by what. Feeds FR-4.
- **Q-3: Should a triage worker be able to call `ghmem unroute`?** The
  allowlist permits it (C-5), so a worker rejected with
  `already_routed` could unroute the item and re-route it. Whether that
  is a containment hole worth closing, and whether the hook is even the
  right place to close it, is unsettled.
- **Q-4: Are the 4 `no-signal` routes in scope?** None of the 22 is
  currently `no-signal`, so the question is only whether `unroute`
  should refuse a `NO_SIGNAL` verdict or treat it uniformly. Uniform
  treatment is the simpler contract.

### References

- `plans/unroute-event/recall-artifact.md` — curated memory entries
- `docs/superpowers/specs/2026-07-09-ghmem-design.md` — ghmem spec,
  sole source of truth for the pipeline's decisions
- `.claude/handoff-task.md` — the re-triage task this event serves
- Corpus measurements taken 2026-09-02 against
  `corpus/cache.sqlite`: 95 routes, 71 topics, 57 singletons, 4
  `no-signal`, 0 items reachable by `requeue` or `reenter`
