# Deliverable Review (Layer 1, prose): ghmem design migration

Reviewed at commit `f3d1276` (plus `cba5073`, `17ad255`) against the
code at `tools/ghmem/src/ghmem/`, the live corpus at
`corpus/cache.sqlite` and `corpus/routing.jsonl`, and the deleted spec
recovered with `git show f3d1276^:docs/superpowers/specs/2026-07-09-ghmem-design.md`.

## Inventory

| file | lines | type | verdict |
| --- | --- | --- | --- |
| `docs/ghmem-design.md` | 656 | living design doc | **Major issues** — 3 false claims about the CLI, 1 unsupported NFR, heavy self-duplication, violates its own NFR9 |
| `docs/ghmem-changelog.md` | 123 | history index | **Critical** — asserts a batch was re-triaged that the ledger shows was never touched |
| `plans/unroute-event/requirements.md` | 314 | requirements | **Sound.** Q-1..Q-4 all check out against the code and the corpus |
| `plans/unroute-event/classification.md` | 91 | triage record | Clean. Its one old-path reference is a correct historical statement |
| `plans/ghmem-design-migration/report.md` | 169 | migration report | **Major** — its verification grep claim is false as printed; 10/10 drift claims themselves verified |
| `plans/ghmem-design-migration/reduction-report.md` | 203 | reduction record | Minor — line-range citations off by 1–2; 36 lines over 80 cols (all table rows) |
| `tools/ghmem/README.md` | 106 | tool README | **Critical** — two stale status claims; command list missing 7 of 13 commands |

Line widths (measured with `awk`):

| file | max | >72 | >80 |
| --- | --- | --- | --- |
| `docs/ghmem-design.md` | 76 | 4 | 0 |
| `docs/ghmem-changelog.md` | 72 | 0 | 0 |
| `plans/unroute-event/requirements.md` | 73 | 3 | 0 |
| `plans/unroute-event/classification.md` | 76 | 6 | 0 |
| `plans/ghmem-design-migration/report.md` | 87 | 4 | 1 |
| `plans/ghmem-design-migration/reduction-report.md` | 304 | 60 | 36 |
| `tools/ghmem/README.md` | 86 | 2 | 1 |

`docs/ghmem-design.md` wraps honestly: max 76 columns, 4 lines over 72,
none over 80, and no evidence of cramming. The migration report's
"No line exceeds 72 characters" (`report.md:139`) is off by 4 lines but
the substance holds.

`reduction-report.md`'s 36 over-80 lines are all markdown table rows
(50–66, 80–92, 105–113), which cannot be hard-wrapped. Not a cramming
violation, but line 50 packs twelve command names plus "semantics, exit
codes, terminal strings" into one 304-character cell, which is
genuinely hard to read.

## Requirements audit: FR1–FR9 / NFR1–NFR10

### FR1 — the corpus holds the whole decision record (`docs/ghmem-design.md:16`)

Testable, mostly singular, solution-free. **Checked:** `ghmem sync`
fetches issues, PRs (with review threads, review comments,
`mergedBy`/`mergedAt`, REST diff), discussions and the commit log —
`sync_items.py`, `sync_discussions.py`, `sync_commits.py`,
`github.py`. True.

**Number problem.** FR1 states "1,245 PRs" verified 2026-07-09. The
corpus holds 1,302 PRs and 2,575 issues:

```
[('issue', 2575), ('pull_request', 1302)]
```

Line 412 of the same document states "1,299 PRs", measured 2026-07-10 —
one day later. 1,245 → 1,299 in a day is not credible, and the corpus
sides with 1,299. Both figures are dated, so neither is *wrong*, but the
document presents 1,245 as the verified scale and never reconciles the
two. See Major 4.

### FR2 — agents read the corpus, never GitHub (`:27`)

Testable and singular, but **its second sentence is not true as a
mechanical guarantee.** "No agent holds a GitHub credential or issues
an API call" reads as enforced, and NFR3 immediately below says
containment "does not rest on the agent following its prompt." The
allowlist hook permits `ghmem sync`, which is the one command that hits
the GitHub API:

```
ALLOW: ghmem sync
ALLOW: ghmem unroute 123
ALLOW: ghmem --session=X unroute
DENY : echo hi
```

The doc's own "Blocking `ghmem unroute` in the containment hook"
rejected alternative (`:601`) enumerates the corpus-wide mutating
commands the shape allowlist already permits — `requeue` and `rebuild`
— and omits `sync`, the only one that breaks FR2. See Major 1.

### FR3 — triage assigns every item a verdict (`:31`)

Not singular: it defines a verdict, reserves `no-signal`, and asserts
where the note surfaces. The third clause is **false for `no-signal`.**

FR3 says the reserved verdict "also carries a note. The note reappears
in the item index `ghmem topic` emits." It does not. `topic.py:102`:

> `note` is always present here: `routed_items` excludes `NO_SIGNAL`
> routes, so every row it returns carries a real-topic verdict

`dispatch_store.py:264` filters `.where(Route.verdict == name,
Route.verdict != NO_SIGNAL)`. `triage.py:293` states the truth: a
`NO_SIGNAL` note "stays on the `Route` row, which is the only place
holding why the item was passed over." See Major 2.

Also unverifiable as written: "triage assigns every item a verdict" is
an end state, not a current property — `status` reports 3,847 pending
at last measurement.

### FR4 — synthesis writes one living document per topic (`:37`)

Testable and true. `docs/design/` holds `INDEX.md` plus five topic docs
(`autocompletion`, `clipboard-behavior`, `debian-packaging`,
`file-tree-sidebar`, `lsp-support`). `INDEX.md` regeneration is
harness-only (`cli.py:178`, `cli.py:327`) and `scope-docs-design.sh`
confines the synthesize agent's writes. Checks out.

### FR5 — every requirement in a topic doc is citation-backed (`:43`)

Testable in principle (read a topic doc, check each FR carries an
issue/PR/commit reference), but nothing in the harness enforces it —
it is an agent-prompt rule. The requirement does not say so, and its
closing sentence ("This is the structural guard against hallucinated
rationale") overstates: it is a prompt-level guard, not a structural
one. **Minor.**

### FR6 — the pipeline is incremental (`:48`)

Testable, singular, true. `cli.py:132` docstring confirms the cursor
resume; `routing_reduce.py:129,140` mark both the leaving and joining
topic dirty; `close_topic` clears only on a matching digest. Checks
out.

### FR7 — a verdict can be corrected in three distinct ways (`:52`)

Testable and true, but not singular — it names four mechanisms
(`route` supersede, `unroute`, `rebuild`/`reenter`, `requeue`) under a
heading that says three. The fourth is flagged as "separately," which
works, but "three distinct ways" then has to be read past. **Minor.**

Substance verified: `cli.py:96` (`unroute`), `cli.py:47` (`rebuild` →
`reenter`), `cli.py:73` (`requeue`), `triage.py:331` (freshness gate).
18 tests across `test_unroute.py` (8), `test_cli_unroute.py` (6),
`test_queue.py` (2), `test_dispatch_store.py` (1),
`test_cli_topic.py` (1).

### FR8 — contributor standing is rendered, never inferred (`:60`)

Testable and true. `show.py` annotates every author against that
entry's own date — item header (`:74`), comments (`:110`), reviews
(`:120`), review comments (`:130`), discussion header (`:211`) — and
`topic.py:105` does the same for the item index. `standing.py`'s module
docstring confirms it never reads `author_association`. Checks out.

### FR9 — backfill covers the whole corpus in heat order (`:64`)

Testable, but it is **not a requirement — it is a verbatim restatement
of a design decision 326 lines below.** Compare:

- FR9 (`:64–66`): "Hottest topics get their docs first, the cold tail is
  triaged last, and the run is abortable at any point with the best
  material banked."
- Design decisions (`:390–392`): "Hottest topics get their docs first
  and the cold tail is triaged last, so the run is abortable at any
  point with the best material banked."

See Major 5.

### NFR1 — fetching stays inside GitHub's rate limits (`:85`)

Testable, singular, true. `github.py:44` `_RATE_LIMIT_SAFETY_MARGIN`,
the `RETRY_SERVER_ERROR`-only retry chain (module docstring lines
13–21) and `RateLimitError` surfacing at `cli.py:142` all implement it.
Checks out.

### NFR2 — all deterministic work lives in the harness (`:93`)

True, but duplicated verbatim by "12-factor split" (`:394`). See
Major 5.

### NFR3 — the agents' data path is mechanically contained (`:97`)

Testable, singular. **Overstated:** true for the tool *shape* (verified
by running the hook, above), but the doc's word "allowlist" invites the
reading that only safe `ghmem` subcommands pass. Every subcommand
passes, including `sync`. The doc's own rejected-alternative section
knows this and the NFR does not say it. See Major 1.

Both agent definitions carry the hook at
`${CLAUDE_PROJECT_DIR}/tools/ghmem/hooks/scripts/ghmem-only.sh`; the
path resolves. `ghmem-synthesize.md` carries the second `Write|Edit`
hook at `scope-docs-design.sh`; that resolves too.

### NFR4 — the CLI meters the batch; a model never counts (`:103`)

True — `budget.py` owns the constants, `serve_next` gates before
serving (`triage.py:120`, `:134`), the agent definitions carry no
budget numbers. Duplicated by "Triage agents pull their own items"
(`:466`). See Major 5.

### NFR5 — ledgers append-only, routing ledger committed (`:107`)

True. `corpus/routing.jsonl` is tracked; `fetch.jsonl` and
`cache.sqlite` are gitignored. Checks out.

### NFR6 — backfill fits a Max5 subscription (`:112`)

Testable only against a token meter nobody has run. Carried over
unchanged from the deleted spec. Fine as a dated estimate; flagged as
unconfirmed.

### NFR7 — heuristic constants are calibrated, never hip-shot (`:116`)

**Untrue as stated.** NFR7 says "Heat weights and standing thresholds
ship as provisional values and are derived by a recorded analysis over
the real corpus," present tense, and the matching decision (`:515`)
elaborates only the heat result. Standing thresholds were never
calibrated:

```
standing.py:20:OWNER_LOGIN = "zyedidia"        # PROVISIONAL — calibrated in subproject 4
standing.py:21:MERGE_THRESHOLD = 10            # PROVISIONAL — calibrated in subproject 4
standing.py:22:ACCEPTED_PR_THRESHOLD = 5       # PROVISIONAL — calibrated in subproject 4
```

`docs/ghmem-heat-weights.md` — the recorded analysis NFR7 rests on —
contains zero occurrences of "standing", `MERGE_THRESHOLD` or
`OWNER_LOGIN`. It fits `HEAT_WEIGHTS` only. See Major 3.

NFR7 also under-scopes: three further heuristic constants are still
provisional and the doc never mentions their status —
`budget.BATCH_ITEMS = 12`, `budget.BATCH_CHARS = 200_000`,
`show_diff.DIFF_BODY_MAX_CHARS = 25_000`. The deleted spec *did* record
this (`oldspec:151` "Both constants are provisional and calibrated
against verdict-accuracy-by-batch-position data"). The migration
dropped it. See Major 3.

### NFR8 — the toolchain is held to the strictest bar (`:120`)

Not singular — it bundles ~8 tool choices, the Python floor and the
GitHub client into one requirement. All individually true
(`pyproject.toml`, `justfile`, `githubkit` in `github.py`). **Minor.**

### NFR9 — 400 lines is the cap on any human-reviewed artifact (`:130`)

Testable, singular, unambiguous — and **the document stating it is 656
lines**, with an exception clause ("Sole exception: PRs that merely
reorganize code with no behavioral change") that does not cover it. The
migration report acknowledges the overage (`report.md:137`) but the
design doc asserts the cap absolutely. See Major 6.

### NFR10 — the pipeline is validated against ground truth (`:134`)

Testable in principle. Its distractor rule and its
semantic-not-textual comparison rule are preserved intact from the
source, and `docs/softwrapcolumn-vs-wrapindent.md` exists. Whether the
gate has been *run* is not stated anywhere; the changelog has no
"validation" slice record and subproject 4 is not indexed. Unconfirmed
— but NFR10's framing ("before it is trusted") makes this a live
precondition, and the corpus already carries 95 routes and five
synthesized topic docs. Worth asking whether the gate ran. **Major 7.**

## Critical

1. **`docs/ghmem-changelog.md:119–123` — asserts a re-triage that never
   happened.** The entry reads:

   > The batch it was built for: the 22 items routed by sessions
   > `BcbBJeUg` and `MZlH9vbE`, triaged before the packet carried a
   > topic list, **are unrouted and re-triaged** by workers that now see
   > every topic minted so far.

   The routing ledger contains **zero** `unroute` events
   (`grep -c '"unroute"' corpus/routing.jsonl` → `0`), and both sessions
   still hold their full route sets:

   ```
   ('BcbBJeUg', 12)
   ('MZlH9vbE', 10)
   ```

   22 routes, untouched. The corpus figures the same paragraph cites as
   the *before* state — 95 routes, 71 topics, 57 singletons — are also
   the figures right now, so a reader gets no signal that anything is
   outstanding. `.claude/handoff-task.md` confirms the re-triage is the
   pending task.

   Why it matters: the changelog is the durable record of what landed.
   Stated in the past/present tense in a dated history entry, this makes
   the next session believe the singleton problem was already addressed
   and skip the work. Either move it to a "planned" framing or run the
   batch before the entry claims it.

2. **`tools/ghmem/README.md:17–19` — "The dispatch subproject … is in
   progress."** It has landed. The changelog records Parts A, B1, B2,
   C1, C2 and D as complete, and `cli.py` exposes `triage`, `route`,
   `topic`, `close`, `index`, `requeue`, `status` and `unroute`. The
   README's own Commands section (`:41–65`) lists only `sync`,
   `rebuild`, `show`, `discussion`, `status`, `heat` — 6 of 13. Seven
   commands are undocumented in the tool's own README. This file was
   touched by `f3d1276` (its design-spec pointer was updated) so the
   staleness was in scope and was missed.

3. **`tools/ghmem/README.md:86–89` — "The weights behind `score` … are
   **provisional** — they are calibrated against the real corpus in the
   validation subproject."** Contradicted by the code and by the design
   doc. `heat.py:8–12`:

   > Calibrated 2026-07-25 against 198 read-only signal labels, and
   > retained: no fitted vector ranked better than this one, and the
   > fitted vector lost to it under cross-validation.

   This is drift item 8 in `plans/ghmem-design-migration/report.md:120`
   — corrected in the design doc, left uncorrected in the README the
   same commit edited. A consumer reading the README will expect the
   weights to change and will treat `score` as unstable when the
   project has decided it is not.

4. **`docs/ghmem-design.md:238` — `ghmem status` does not report
   "stuck" items.** The bullet says status emits "counts of triaged,
   routed, no-signal, pending, dispatched and stuck items." The actual
   output (`status.py:69–77`) is:

   ```
   total=… triaged=… routed=… no_signal=… pending=… dispatched=…
   ```

   `StatusSummary` (`status.py:14–27`) has no `stuck` field, and `grep
   -rn "stuck" tools/ghmem/src` returns nothing. `dispatched` *is* the
   stranded count (`dispatched_unrouted()`), so the doc names two
   outputs where one exists, and omits `total`. This is an eleventh
   drift item, inherited verbatim from the deleted spec
   (`oldspec:285`), that the migration's ten-item sweep missed.

5. **`docs/ghmem-design.md:422–424` — a `fit` dispatch is not tagged in
   the packet.** The doc says:

   > Every dispatch is tagged with which of the three happened — `fit`,
   > `diff_cut`, `oversized` — in the packet and in the ledger

   In the ledger, yes (`triage.py:141`). In the packet, no.
   `_render_packet` (`triage.py:213–215`) emits a `packet-note:` line
   only when `note is not None`, and the `fit` path never passes one
   (`triage.py:129`). The two notes that *are* emitted are prose
   (`triage.py:218–221`), not the tokens `diff_cut` / `oversized`. The
   B2 spec states the truth explicitly at
   `docs/superpowers/specs/2026-07-22-ghmem-b2-solo-item-fit-design.md:127`:
   "A packet whose outcome is not `fit` carries one extra header line."
   Twelfth uncorrected drift item, again inherited verbatim
   (`oldspec:263–266`).

## Major

1. **`docs/ghmem-design.md:27–29` and `:97–100` — FR2/NFR3 claim a
   mechanical no-GitHub guarantee the hook does not deliver.** The
   allowlist is a command-*shape* filter; `ghmem sync` passes it and
   hits the API (verified by running the hook). The doc's own rejected
   alternative at `:601–612` enumerates `requeue` and `rebuild` as the
   permitted mutating commands and omits `sync` — the only one that
   matters for FR2. Concretely: a reader deciding whether an agent needs
   a credential-free environment will conclude the hook suffices. It
   does not; FR2 rests on the agent prompt, which is exactly what NFR3
   says containment does not rest on.

2. **`docs/ghmem-design.md:34–35` — FR3's "The note reappears in the
   item index `ghmem topic` emits" is false for `no-signal`.**
   `routed_items` excludes `NO_SIGNAL` (`dispatch_store.py:264`,
   `:352`), so a `no-signal` note appears in no rendered output at all —
   only on the `Route` row. FR3's paragraph structure puts the sentence
   right after the `no-signal` clause, so it reads as covering both. A
   reader auditing why an item was passed over will look in `ghmem
   topic` output and find nothing.

3. **`docs/ghmem-design.md:116–118` — NFR7's standing-thresholds claim
   is unsupported, and three provisional constants lost their recorded
   status in the migration.** `standing.py:20–22` still carries
   `PROVISIONAL — calibrated in subproject 4` on all three thresholds;
   `docs/ghmem-heat-weights.md` never mentions standing. Separately,
   `BATCH_ITEMS`, `BATCH_CHARS` and `DIFF_BODY_MAX_CHARS` are still
   provisional and the living doc records neither their values nor their
   status, where the deleted spec did (`oldspec:151`). A living design
   doc that asserts calibration is done is the worst outcome here: it
   retires a real open item silently.

4. **`docs/ghmem-design.md:20` vs `:412` — two irreconcilable PR
   counts, 1,245 (2026-07-09) and 1,299 (2026-07-10).** The corpus holds
   1,302, so 1,299 is right and FR1's scale figure is ~4.5% low. Both
   were inherited from the deleted spec, but the restructure put them in
   one document with no reconciliation. A reader sizing a run against
   FR1 gets the wrong denominator.

5. **`docs/ghmem-design.md` — five of nineteen requirements are verbatim
   restatements of design decisions below them.** This is the largest
   single contributor to the 656-line length:

   | requirement | duplicated by |
   | --- | --- |
   | FR9 `:64–67` | "Backfill: full coverage, heat-ranked order" `:390–392` |
   | NFR2 `:93–95` | "12-factor split" `:394–397` |
   | NFR3 `:97–100` | "Triage and synthesize are custom agents" `:435–438` |
   | NFR4 `:103–105` | "Triage agents pull their own items" `:466–484` |
   | NFR7 `:116–118` | "Formula constants are calibrated" `:515–517` |

   FR7's `unroute` content is additionally restated by the `ghmem
   unroute` architecture bullet (`:218–229`), the "Unrouting a topic's
   last item" decision (`:504–513`) and four Rejected-alternatives
   entries. Concretely, this is how NFR7's standing-thresholds error got
   past review twice: the same false claim appears in two places and
   neither prompted a check.

6. **`docs/ghmem-design.md:130–132` — the document violates its own
   NFR9 and states the cap absolutely.** 656 lines against a stated
   400-line cap whose "sole exception" is code-reorganization PRs. Two
   honest resolutions: soften NFR9 to a soft budget (which is how the
   project actually applies it elsewhere), or split. On the seam: see
   the assessment below — **I recommend deduplication over the split.**

7. **NFR10's validation gate has no recorded run.** The changelog
   indexes subprojects 1–3's slices and the heat calibration, but
   nothing for subproject 4's wrapindent ground-truth gate, and NFR10
   frames it as a precondition ("before it is trusted"). Meanwhile 95
   items are routed and five topic docs are synthesized. Either the gate
   ran and is unrecorded, or the pipeline is being trusted ahead of its
   own stated gate. **Unconfirmed** — I could not find evidence either
   way; worth one direct answer.

8. **`plans/ghmem-design-migration/report.md:152–158` — the verification
   grep claim is false as printed.** The report prints the command and
   says "Returns nothing." Run verbatim:

   ```
   $ grep -rl "2026-07-09-ghmem-design" --include="*.md" . | grep -v "^./.git/"
   .claude/handoff-task.md
   plans/ghmem-design-migration/report.md
   plans/unroute-event/classification.md
   ```

   Three hits. None is a broken link — `classification.md:66–67` is a
   deliberate historical statement ("was X … and has since been migrated
   to Y"), and the other two are self-references — so reference
   integrity is genuinely fine. But the report's stated evidence does
   not reproduce, which is the class of claim a later session will lean
   on without re-running.

   Related: the same paragraph says "Thirteen files were patched … five
   plans and four specs under `docs/superpowers/`,
   `memory/ghmem-project.md`, `tools/ghmem/README.md`, and both
   `plans/unroute-event/` files." That is 13 files, but `git show
   f3d1276 --stat` shows 9 under `docs/superpowers` (10 reference lines,
   `agents-design.md` carrying two) plus the README. The
   `plans/unroute-event/` files were not patched — `classification.md`
   still carries the old path by design. The commit message's "All
   thirteen inbound references were rewritten" inherits the same
   miscount.

## Minor

- `docs/ghmem-design.md:39,286` — FR4 and Architecture §2 both spell out
  the FR/NFR/Limitations/Open questions/History structure; §2 alone
  suffices.
- `docs/ghmem-design.md:43–46` — FR5's "structural guard" overstates a
  prompt-level rule; nothing in the harness enforces citation backing.
- `docs/ghmem-design.md:52` — FR7 says "three distinct ways" then names
  four mechanisms.
- `docs/ghmem-design.md:120–128` — NFR8 bundles ~10 independent
  toolchain choices into one requirement; not singular.
- `docs/ghmem-design.md:31` — "triage assigns every item a verdict" is
  an end state, not a property currently true (3,847 pending).
- `docs/ghmem-design.md:243–245` — the `ghmem topic` item-index field
  list omits the comment count that `topic.py:110` renders.
- `docs/ghmem-design.md:217,457–464` — the doc never records that
  `_read_note` (`cli.py:285–286`) **rejects a multi-line note**. Given
  the whole heredoc apparatus exists to carry prose, a writer will hit
  this. Worth one clause.
- `docs/ghmem-design.md:238` — the status listing omits `total`.
- `tools/ghmem/README.md:79` — 86 columns (table row, unavoidable).
- `plans/ghmem-design-migration/report.md:139` — "No line exceeds 72
  characters" is off by 4 lines (max is 76).
- `plans/ghmem-design-migration/reduction-report.md:5` — "1,890 lines
  down to 88"; `wc -l memory/ghmem-project.md` reports 90. The 3,999
  bytes figure is exact.
- `plans/ghmem-design-migration/reduction-report.md:50–66` — line-range
  citations into the design doc run 1–2 lines low throughout (`:51` says
  storage is 264–277, it is 262–277; `:52` says 456–465, it is 457–464;
  `:53` says 444–455, it is 440–455; `:55` says line 314, file-watched
  is 315). Consistent off-by-one suggests it was written against a
  slightly earlier draft. Harmless individually, but every one of these
  is a pointer a later session will follow.

## The changelog/design split

**Clean, with one exception, and the exception is the Critical above.**

`docs/ghmem-changelog.md` carries only history: the four-subproject
split, twelve dated slice records with their frozen spec/plan pointers,
and the 2026-09-02 `unroute` entry. `docs/ghmem-design.md` carries only
current state. I found **no** self-referential draft archaeology in the
design doc — no "was confusingly similar", no "the earlier constraint is
dropped", no comparisons against a prior version. The one backward
reference is `:12` ("Its own change history is indexed in
`docs/ghmem-changelog.md`"), which is a pointer, not archaeology.

Genuine rejections of design alternatives are correctly **kept** and
should stay: "Shelling out to `gh api graphql`" (`:586`), "An existing
tool" (`:576`), "A companion procedure skill" (`:592`), "A deny-pattern
hook" (`:597`), "Blocking `ghmem unroute` in the containment hook"
(`:601`), "A `--session` selector on `unroute`" (`:614`), "Refusing to
unroute a `no-signal` verdict" (`:620`), "Relaxing `record_verdict`"
(`:624`), "Dispatch timeouts" (`:629`), "Digest-only tiers" (`:634`),
"Delta machinery" (`:639`), "GH Archive" (`:643`), "Heat-ordered
`INDEX.md`" (`:649`). These are decisions with standing rationale. Do
not cut them.

The changelog's `unroute` entry (`:96–123`) is the one place the split
leaks: the *design* of the event (what it deletes, the emptied-topic
rule, the three rejections) is restated there in full alongside the
design doc's own copy at `:218–229`, `:504–513` and `:601–627`. The
migration report says the intent was for the changelog to carry only
"the batch that motivated it" (`report.md:84–86`). It carries both.

## Design doc vs code drift, in the other direction

**`routing_reduce.py`: the migration report is correct.** The design
doc names no reducer module — `grep -n "ingest\|routing_reduce\|reducer"
docs/ghmem-design.md` returns only `:510` ("the reducer writes SQLite
and is replayed by `rebuild`"), which is true of `routing_reduce.py`.
The Architecture section describes commands, not modules, so the
`17ad255` split created no drift. Checks out.

**New drift the migration introduced or failed to catch:** Critical 4
(`stuck` status count) and Critical 5 (`fit` packet tag) — both
inherited verbatim from the deleted spec and both in the exact class
the ten-item sweep was looking for. Plus Major 3 (three provisional
constants whose status the doc dropped).

**Numbers that were true when measured and are not now:** FR1's corpus
scale, all dated 2026-07-09, against a corpus now holding 2,575 issues
/ 1,302 PRs / 3,529 commits / 245 discussions. Dated, so not wrong —
but see Major 4 for the internal contradiction.

## Numbers

All cross-document figures checked against the live corpus with a
read-only SQLite connection.

| figure | cited in | verified |
| --- | --- | --- |
| 95 routes | changelog `:122`, requirements `:313` | **95** ✓ |
| 71 topics | changelog `:122`, requirements `:71,:313` | **71** ✓ (and 71 distinct non-`no-signal` verdicts) |
| 57 singletons | changelog `:123`, requirements `:70,:313` | **57** ✓ |
| 4 `no-signal` | changelog `:116`, requirements `:211,:303,:314` | **4** ✓ |
| 0 empty `Topic` rows | requirements `:269–270` | **0** ✓ |
| 1802-char topic list | requirements `:207` | **1802** ✓ exactly (`'topics: ' + ', '.join(71 names)`) |
| `BATCH_CHARS` = 200,000 | requirements `:208` | **200_000** ✓ (`budget.py:17`) |
| 36.5% calibrated base rate | changelog `:116`, requirements `:211,:303` | ✓ traced to `docs/ghmem-heat-weights.md:241,313` |
| `ingest.py` 111 / `routing_reduce.py` 328 | requirements `:134` | **111 / 328** ✓ |
| `dispatch_store.py` 355 | requirements `:127` | **355** ✓ |
| `memory/ghmem-project.md` 3,999 bytes | reduction-report `:5` | **3,999** ✓ (line count 90, not 88) |
| 22 items in the two sessions | changelog `:119`, requirements `:105` | **22** ✓ (12 + 10) |
| 1,245 PRs | design `:20` | ✗ corpus holds **1,302**; see Major 4 |
| 1,299 PRs | design `:412` | ✓ consistent with corpus |
| 2,569 issues | design `:20` | corpus holds 2,575 — dated figure, consistent |
| 3,524 commits | design `:21` | corpus holds 3,529 — dated figure, consistent |
| 243 Discussions | design `:21` | corpus holds 245 — dated figure, consistent |
| 656 / 123 lines | report `:137,:140` | **656 / 123** ✓ |

**Could not verify:** NFR6's ~3–4M-token corpus and the 5M/3–6M
triage/synthesis estimates (no token meter run); NFR1's ~7–10k REST call
estimate; the 2026-07-10 diff measurements (median 16 lines, p99 ≈ 1.6k,
10 of 1,299 PRs over 2k). All are dated and inherited unchanged.

## Requirements audit: `plans/unroute-event/requirements.md` Q-1..Q-4

All four decisions check out. This is the strongest document in the set.

**Q-1 (positional variadic numbers).** `cli.py:95` —
`nargs=-1, required=True`, matching `show`/`route`. Its claim that "the
allowlist hook's argument class permits either form" is **verified by
execution**: `ALLOW: ghmem --session=X unroute`. The `arg` class
`[A-Za-z0-9_./:=-]+` (`ghmem-only.sh:40`) admits both `-` and `=`. Its
claim that the 22 numbers are "derivable once with a query over
`Route.session`" is verified — `route` carries a `session` column
(`['item_id','verdict','note','session','item_updated_at']`).

**Q-2 (delete an emptied `Topic` row).** Verified.
`routing_reduce.py:200,218–221` implements it, scoped to `unroute`. The
supporting corpus claim — "the mirror holds zero empty `Topic` rows
across 95 routes" — verified: `0`. The claim that `docs/design/<name>.md`
is never removed is verified (no filesystem write in the reducer). The
"three tests in `test_routing_replay.py` plus two in
`test_routing_live.py`" count I could not pin exactly to five specific
tests — `test_routing_replay.py:87` is clearly one of them — so I label
that sub-claim **unconfirmed**, not wrong.

**Q-3 (no containment change).** Verified on both legs.
`record_verdict`'s `not_dispatched` guard is at `triage.py:327–329`, and
the hook does already permit `ghmem requeue` and `ghmem rebuild` — it is
a shape allowlist, confirmed by reading `ghmem-only.sh:41` and by
running it. The reasoning is sound and the "no incident has occurred;
the rule waits for one" framing is right.

**Q-4 (uniform treatment of `no-signal`).** Verified.
`routing_reduce.py:218–221` skips the topic-release step for
`NO_SIGNAL`, matching the `route` and `reenter` branches at `:129` and
`:200`. The 4-of-95-against-36.5% figure traces to
`docs/ghmem-heat-weights.md`.

One caveat on the requirements doc, not a defect: **FR-6 ("The batch
this is built for is re-triaged") is unmet.** Its acceptance criterion
— "All 22 carry a `Route` written by a session other than those two" —
fails. That is fine for a requirements document (it states intent), but
it is the same fact the changelog wrongly reports as done (Critical 1).

## Size: the split seam

`Design decisions` runs `:373–573` (201 lines); `Rejected alternatives`
runs `:574–651` (78 lines). 279 together, leaving ~377.

**I recommend not taking that seam.** Two reasons:

1. **It would split duplicated content rather than removing it.** Five
   of the nineteen requirements are verbatim copies of Design-decisions
   paragraphs (Major 5), and the Architecture bullets for `unroute`,
   `triage`, `close` and `heat` each restate their own rationale inline.
   Splitting sends one copy to each file, so a reader now has to check
   two documents to know whether they disagree — and this review found
   that two such copies (NFR7 and "Formula constants") *already* carry
   the same false claim, unnoticed precisely because they duplicate.

2. **Deduplication gets under 400 without a split.** Removing the five
   duplicated requirements and folding the Architecture bullets'
   rationale into the decisions they repeat is worth roughly 100–150
   lines, and unlike a split it removes the drift surface rather than
   doubling it. The project's own convention is that the rationale sits
   beside what it justifies — which is the argument
   `report.md:148–150` makes for keeping the doc whole, and I agree with
   it.

If a split is taken anyway, the seam is the right one — but do the
deduplication first regardless, because it is what the review burden
actually comes from.

## Reference integrity

**Grepped for the old spec path** across the whole tree (`grep -rn
"2026-07-09-ghmem-design"`, excluding `.git`):

| hit | verdict |
| --- | --- |
| `plans/ghmem-design-migration/report.md:3,155,163` | self-reference in the migration report; correct |
| `plans/unroute-event/classification.md:66` | deliberate historical statement, immediately followed by ":67 … and has since been migrated to `docs/ghmem-design.md`"; correct |
| `.claude/handoff-task.md:16` | task-state file describing the migration; correct |

**No dangling reference.** All ten repointed references resolve:

```
docs/superpowers/plans/2026-07-11-ghmem-corpus-read.md
docs/superpowers/plans/2026-07-11-ghmem-corpus-store.md
docs/superpowers/plans/2026-07-11-ghmem-corpus-sync.md
docs/superpowers/plans/2026-07-15-ghmem-corpus-discussions.md
docs/superpowers/plans/2026-07-17-ghmem-dispatch-heat-status.md
docs/superpowers/specs/2026-07-23-ghmem-c1-index-design.md
docs/superpowers/specs/2026-07-24-ghmem-agents-design.md      (×2)
docs/superpowers/specs/2026-07-24-ghmem-c2-topic-close-design.md
docs/superpowers/specs/2026-08-11-ghmem-d-reentry-design.md
```

`memory/ghmem-project.md:12` points at `docs/ghmem-design.md`. ✓

**Every path named in `docs/ghmem-design.md`** was resolved
programmatically:

```
OK   .claude/agents/ghmem-synthesize.md
OK   .claude/agents/ghmem-triage.md
OK   corpus/cache.sqlite
OK   corpus/fetch.jsonl
OK   corpus/routing.jsonl
OK   docs/design/INDEX.md
OK   docs/ghmem-changelog.md
OK   docs/ghmem-heat-weights.md
OK   docs/softwrapcolumn-vs-wrapindent.md
```

(`INDEX.md` and `routing.jsonl` also appear bare in prose; not path
claims.) `tools/ghmem/hooks/scripts/ghmem-only.sh` and
`scope-docs-design.sh` both exist. All 13 `ghmem` subcommands named in
the doc exist in `cli.py`. Both agent-frontmatter hook paths resolve.

Changelog pointers: all 14 spec/plan paths under `docs/superpowers/`
plus `docs/ghmem-heat-weights.md` and `docs/ghmem-tail-sample.md`
exist.

One inbound reference **not** repointed, outside this commit's scope but
worth noting: `tools/ghmem/hooks/scripts/ghmem-only.sh:5` cites
`docs/superpowers/specs/2026-07-24-ghmem-agents-design.md` — that file
still exists, so it resolves.

## Verification log

```
awk '{if(length($0)>m)m=length($0)}END{print m}'  # per-file max width
git show f3d1276 --stat -- docs/superpowers        # 9 files, 10 ref lines
git show f3d1276^:docs/superpowers/specs/2026-07-09-ghmem-design.md > /tmp/claude/oldspec.md
grep -rn "2026-07-09-ghmem-design" .               # 3 hits, all benign
grep -rl "2026-07-09-ghmem-design" --include="*.md" . | grep -v "^./.git/"
```
→ report claims "Returns nothing"; returns 3 files (Major 8).

```
python3 -c "sqlite3 … 'file:corpus/cache.sqlite?mode=ro'"
```
→ `routes 95`, `topic 71`, `distinct real topics 71`, `no_signal 4`,
`singletons 57`, `issues+prs 3877`, `discussions 245`, `commits 3529`
→ `[('issue', 2575), ('pull_request', 1302)]`
→ empty topics `0`
→ route columns `['item_id','verdict','note','session','item_updated_at']`
→ topic line = **1802** chars over 71 names
→ route sessions: `BcbBJeUg` 12, `MZlH9vbE` 10 (22 total, intact)

```
grep -c '"unroute"' corpus/routing.jsonl
```
→ `0` (Critical 1).

```
for c in "ghmem sync" "ghmem unroute 123" "ghmem --session=X unroute" "echo hi"; do
  jq -nc --arg c "$c" '{tool_input:{command:$c}}' | bash tools/ghmem/hooks/scripts/ghmem-only.sh
done
```
→ `ALLOW: ghmem sync` / `ALLOW: ghmem unroute 123` /
`ALLOW: ghmem --session=X unroute` / `DENY : echo hi`
(Major 1; Q-1 confirmed).

```
grep -n "PROVISIONAL\|calibrat" tools/ghmem/src/ghmem/*.py
```
→ `budget.py:16,17`, `standing.py:20,21,22`, `show_diff.py:19` still
PROVISIONAL; `heat.py:8–12` calibrated and retained (Major 3;
Critical 3).

```
grep -n "standing\|MERGE_THRESHOLD\|OWNER_LOGIN" docs/ghmem-heat-weights.md
```
→ no output (Major 3).

```
grep -rn "stuck" tools/ghmem/src
```
→ no output (Critical 4).

```
grep -rn "36.5" docs/ plans/ tools/ghmem/src
```
→ `docs/ghmem-heat-weights.md:241,313` — grounded.

```
wc -c -l memory/ghmem-project.md   →  90 lines, 3999 bytes
grep -rc "def test.*unroute" tools/ghmem/tests/*.py  →  18 tests
```

Files read in full: `docs/ghmem-design.md`, `docs/ghmem-changelog.md`,
`plans/unroute-event/requirements.md`,
`plans/unroute-event/classification.md`,
`plans/ghmem-design-migration/report.md`,
`plans/ghmem-design-migration/reduction-report.md`,
`tools/ghmem/README.md`, `src/ghmem/cli.py`, `src/ghmem/triage.py`,
`src/ghmem/topic.py`, `src/ghmem/budget.py`, `src/ghmem/heat.py`,
`src/ghmem/paths.py`, `src/ghmem/status.py`,
`hooks/scripts/ghmem-only.sh`, both agent definitions, and excerpts of
`src/ghmem/standing.py`, `src/ghmem/show.py`,
`src/ghmem/dispatch_store.py`, `src/ghmem/github.py`.

**Not verified:** `just green` / `just precommit` were not run (this is
a prose review and no source file was edited). NFR6's and NFR1's token
and call-count estimates. Whether NFR10's wrapindent gate has ever run.
Q-2's "three tests plus two tests" count.
