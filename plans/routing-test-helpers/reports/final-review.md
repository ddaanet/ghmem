# Final review — `plans/routing-test-helpers/runbook.md`

Verdict: **PASS.** No fixes applied — nothing to fix. The diff
`git diff 35b9799 556c51f` is a verbatim move plus import-line edits, and the
two deviations the runbook mandates are both present and are the only ones.

Gate re-run from the repository root after deleting
`tools/ghmem/tmp/.green-sentinel` to defeat the cache (the executor's own run
had left it valid, so an unforced `just precommit` printed
`✓ Cached (inputs unchanged)` — not evidence):

```
✓ ruff check · docformatter -c · mypy · 278 passed · 47 bats · line limits
```

`git status --porcelain` is empty; nothing was committed by this review.

## Check 1 — no test added, removed, retargeted or renamed; no assertion edited

**PASS.** Verified against the diff, not the report.

Tree-wide, the set of test-function definition lines under `tools/ghmem/tests`
is character-for-character identical across the two commits:

```
diff <(git grep -h "^def test_\|^async def test_" 35b9799 -- tools/ghmem/tests | sort) \
     <(git grep -h "^def test_\|^async def test_" 556c51f -- tools/ghmem/tests | sort)
```

produced no output, as did the same comparison over every `parametrize` line
(collection count depends on those too). Per-file, extracting `^def test_` and
`^    assert` lines before and after differs only in the line numbers each sits
at — every def and every assertion string is unchanged; the shifts are the
deleted helper blocks above them.

Reading the diff hunks directly confirms it: every hunk is either a deletion of
a helper definition / module constant, or an edit to an import line. No hunk
touches a test body.

Byte-identity of the moved definitions, each origin against
`tools/ghmem/tests/routing_helpers.py`:

- `_live_store` + `_session` block — identical from all four of
  `test_reenter.py`, `test_requeue.py`, `test_routing_live.py`,
  `test_unroute.py` (`diff` empty in each case).
- `_dispatch` + `_route` from `test_unroute.py` — identical (the only delta is
  two trailing blank lines that were the separator before `UNROUTE_1`).
- `DB_NAME = "cache.sqlite"` and
  `FETCH_FIXTURE = Path(__file__).parent / "fixtures" / "routing_fetch.jsonl"` —
  identical.

## Check 2 — test count unchanged

**PASS.** Current: `278 tests collected` from `pytest --collect-only -q`, and
`278 passed` from the forced gate run.

The pre-change figure is established structurally rather than by checking the
old tree out: collection is per-file, only these six files changed, and check 1
shows the tree-wide set of test definitions and of `parametrize` lines is
identical at both commits. A move that leaves both sets untouched cannot change
the collected count. Per-file def counts also match one-for-one
(`test_reenter` 5, `test_requeue` 6, `test_routing_live` 4,
`test_routing_replay` 11, `test_unroute` 9).

## Check 3 — the two mandated substitutions

**PASS on both.**

*Stale docstring replaced, not absorbed.* The old `test_routing_replay.py`
`_session` carried:

```
    `Store` doesn't expose `Route`/`Dispatch`/`Topic` queries yet (that's
    Task 4) — this reopens the same on-disk database directly so tests
    can assert via plain `select`/`session.get`.
```

The shared module's `_session` carries the one-line docstring the other four
copies had ("Open a second session onto the same SQLite file the store uses.")
and nothing of the above. Grepping the whole tree for `doesn't expose` returns
only two hits, both prose citations in this plan's own documents — the runbook
at line 49 and `reports/item-1-1.md` at line 20. It survives nowhere in code.

*Not-collected note present.* `routing_helpers.py`'s module docstring opens
"Shared fixtures for the split routing test modules." and states "Not collected
by pytest — the module carries no `test_` prefix.", matching
`triage_helpers.py`'s wording verbatim, then names its five importers and cites
`sync_helpers`'s precedent — the same shape `triage_helpers.py` uses.

## Check 4 — scope respected

**PASS.** `git diff --name-only` lists exactly the six files in the dispatch;
nothing else in the tree was touched.

- `DISPATCH_1`, `ROUTE_SOFTWRAP`, `ROUTE_WRAPPING` in
  `tools/ghmem/tests/test_routing_live.py` — extracted and diffed against
  35b9799: unchanged.
- `_corpus_session` and the `corpus` fixture in
  `tools/ghmem/tests/test_requeue.py` — extracted and diffed: unchanged.
  `test_requeue.py` accordingly keeps its own `create_engine` / `Session`
  imports, which `_corpus_session` still needs.
- `test_cli_unroute.py`, `test_dispatch_store.py`, `test_stale_routes.py`,
  `test_status.py` — `git diff --quiet` clean for each.

## Check 5 — import hygiene

**PASS.** The import edits are confined to import lines in every file (visible
in the diff: no hunk mixes an import change with a body change).

Counting every occurrence of each candidate symbol per file shows no import
left behind unused and none dropped while still needed — each surviving import
has at least one use beyond its own import line:

| File | Kept and still used | Dropped |
|---|---|---|
| `test_reenter.py` | `FETCH_FIXTURE` (2 uses), `RoutingEvent`, `Ledger`, `select` | `create_engine`, `Session`, `FetchEvent`, `Store` |
| `test_requeue.py` | `DB_NAME` (1 use, in `_corpus_session`), `FETCH_FIXTURE`, `create_engine`, `Session`, `RoutingEvent` | `FetchEvent`, `Store` |
| `test_routing_live.py` | `FETCH_FIXTURE` (2), `FetchEvent` (1), `RoutingEvent`, `RouteEvent` | `create_engine`, `Session`, `Store` |
| `test_routing_replay.py` | `DB_NAME`, `FETCH_FIXTURE`, `FetchEvent`, `Store` (its `store` fixture) | `create_engine`, `Session` |
| `test_unroute.py` | `FETCH_FIXTURE`, `_dispatch`, `_route`, `_live_store`, `_session` | `create_engine`, `Session`, `FetchEvent`, `RoutingEvent`, `Store`, `DB_NAME` |

The reverse failure — a name used but no longer imported — is additionally
ruled out by ruff (F821) and mypy in the forced gate run, both clean.

## Check 6 — import form matches the repo convention

**PASS.** All five consumers use `from tests.routing_helpers import ...`,
matching `from tests.sync_helpers import ...` in `test_sync.py`,
`test_sync_items.py`, `test_sync_commits.py`, `test_sync_discussions.py` and
`from tests.triage_helpers import ...` in the four triage modules.
`test_unroute.py` uses the parenthesized multi-line form, as
`test_triage.py:26` and `test_triage_fit.py:29` already do for the same reason
(five names).

## Check 7 — line counts

Measured with `wc -l` on the post-change tree; "before" from
`git show 35b9799:`. Cap is `MAX_LINES=400` in
`tools/ghmem/scripts/check_line_limits.sh`.

| File (`tools/ghmem/`) | Before | **After** | Δ | Headroom to 400 |
|---|---|---|---|---|
| `tests/test_routing_live.py` | 186 | **168** | −18 | 232 |
| `tests/test_routing_replay.py` | 340 | **328** | −12 | 72 |
| `tests/test_unroute.py` | 308 | **267** | −41 | 133 |
| `tests/test_reenter.py` | 188 | **170** | −18 | 230 |
| `tests/test_requeue.py` | 260 | **244** | −16 | 156 |
| `tests/routing_helpers.py` | — | **60** | +60 | 340 |

105 lines came out of the five consumers, against the runbook's estimate of
"about 60" — the estimate counted one copy of each helper rather than the
duplicated copies. Net change across the tests directory is −45.

For the downstream `plans/reroute-empty-topic/`: its two targets are
`test_routing_live.py` at **168** (232 lines of headroom) and
`test_routing_replay.py` at **328** (72 lines). Replay is the tight one — it
was already the largest file before the extraction and gained the least from
it, since it carried only `_session`.

## Nothing unfixable

The runbook's stop condition ("any assertion edit needed to keep the suite
green means the move was not a move") was not reached. No behaviour change
slipped in.
