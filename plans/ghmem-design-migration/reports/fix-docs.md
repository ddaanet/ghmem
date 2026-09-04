# Fix report: changelog, README, migration reports

All eleven findings applied. Four files touched:
`docs/ghmem-changelog.md`, `tools/ghmem/README.md`,
`plans/ghmem-design-migration/report.md`,
`plans/ghmem-design-migration/reduction-report.md`.

## Applied

### F1 — the changelog no longer claims the re-triage ran

Verified first, from `/Users/david/code/micro`:

```
grep -c '"unroute"' corpus/routing.jsonl
```

printed `0`. A pass over the ledger confirms the shape:
`Counter({'route': 103, 'dispatched': 95, 'snapshot': 6, 'close': 5})`,
and per-session route counts `BcbBJeUg` 12, `MZlH9vbE` 10 — the full
22 still in place. The SQLite mirror agrees: 95 rows in `route`, 72
distinct verdicts of which 57 are singletons, 71 `topic` rows, 4
`no-signal`. So the changelog's before-figures were also its current
figures.

The `unroute` entry now says the batch is "the motivating case, not a
landed one", keeps the 22 items, both session ids and the
95/71/57 measurement, and closes with the check rather than a claim
that rots: "Whether the batch has since run is a question for the
ledger — `grep -c '"unroute"' corpus/routing.jsonl` from the
repository root answered `0` when this entry was written."

### F2 — README "What exists today"

Rewritten to describe a complete pipeline: `sync` → ledger,
`rebuild` → mirror, the read commands over the mirror, and the
dispatch loop (`triage`/`route`, `topic`/`close`, `index`) with
`requeue` and `unroute` as its correction paths. What remains
outstanding is stated as coverage rather than machinery, pointing at
`ghmem status` instead of quoting a figure that will drift.

Also fixed in the same paragraph, unprompted: line 8 said
`docs/ghmem-design.md` is "at the repository root", which it is not.
It now says "in the repository's `docs/` directory", and names
`docs/ghmem-changelog.md` as the dated record.

### F3 — the seven undocumented commands

Added `requeue`, `triage`, `route`, `unroute`, `topic`, `close`,
`index`, each written from `cli.py` and the module behind it
(`triage.py`, `topic.py`, `status.py`, `dispatch_store.py`) — not from
`docs/ghmem-design.md`, which was not opened. Commands section now
runs `sync`, `rebuild`, `requeue`, `show`/`discussion`, `status`,
`heat`, `triage`, `route`, `unroute`, `topic`, `close`, `index`. The
`ghmem heat` CSV contract table is untouched.

The one-thing-a-caller-gets-wrong for each:

- `requeue` — writes to `routing.jsonl`, so it belongs before a run
  starts a session, never mid-session; it serves nothing.
- `triage` — the three terminal strings go to **stderr with exit 0**,
  and the `triage complete:` / `batch complete:` prefixes mean
  different things to an orchestrator (`triage.py:120–163`).
- `route` — the note is stdin-only, single-line, required for every
  verdict including `no-signal`; a rejection exits 1 with the ledger
  untouched (`cli.py:248–290`).
- `unroute` — all numbers resolved before any event is appended, a
  number with no verdict is not an error, and it takes no session
  token (`cli.py:96–131`).
- `topic` — the snapshot token is what `close` compares against; a
  name with no routed items exits 1 (`topic.py:34–60`).
- `close` — stale is exit **0** on stderr, unknown token is exit 1
  (`cli.py:312–339`).
- `index` — `close` already runs it, so a direct call is for after a
  hand edit; it creates `docs/design/` and an empty index is valid.

`status`'s entry gained the actual counts line it emits,
`total triaged routed no_signal pending dispatched`
(`status.py:68–78`).

### F4 — heat weights

Read `tools/ghmem/src/ghmem/heat.py:1–30`. The module docstring says
"Calibrated 2026-07-25 against 198 read-only signal labels, and
retained: no fitted vector ranked better than this one, and the fitted
vector lost to it under cross-validation", and the comment above
`HEAT_WEIGHTS` adds that only `comments` has a coefficient the
calibration could resolve.

The README now states exactly that, drops "provisional", points at
`docs/ghmem-heat-weights.md`, and keeps the standing advice that
consumers depend on the per-metric columns rather than `score` —
now justified by the four unresolved coefficients rather than by a
calibration that has not happened.

### F5 — the `unroute` entry gets a pointer and the list's format

The `## 2026-09-02 — the unroute event` H2 is gone; the entry is a
bullet in **Slice records**, ending in
`plans/unroute-event/requirements.md`. The file now has one entry
format below the 2026-07-09 section.

Compressed as instructed: what `unroute` does, what its reducer
deletes, and the emptied-`Topic` rule in one clause — the four
decisions, the three freshness gates and the "no guard was weakened"
argument are dropped, since the frozen record and the living doc carry
them. `docs/ghmem-design.md` was not read.

The header contract needed one word: it promised "the spec or plan
written at the time", and the packet-topic-list entry (F6) has
neither. It now reads "the spec, plan or commit written at the time".

### F6 — two missing entries

- **2026-09-02, the triage packet carries the topic list.** From
  `git show 92d1283 --stat` and its message: the `topics:` line names
  every topic minted so far and sits inside the budgeted body, not the
  free header, so an oversized first item's diff allowance is computed
  over a floor that already carries it. Motivation kept (workers were
  told to check a list no packet carried; two sessions minted two
  fresh clipboard names beside an existing `clipboard-behavior`).
  Pointer: the commit itself, flagged as such.
- **2026-09-03, `docs/ghmem-design.md` becomes the living design
  doc.** From `git show f3d1276 --stat` and its message: the move off
  the dated slice-spec path, this file taking over the frozen records,
  the restructure, ten drift corrections, and FR1–FR9 plus
  NFR1–NFR10 newly authored. Pointer:
  `plans/ghmem-design-migration/report.md`.

Both are dated by commit date (`92d1283` Sep 2, `f3d1276` Sep 3). The
existing `unroute` entry keeps its 2026-09-02 date even though
`17ad255` landed Sep 3 — that is the date its requirements were
written, and it was not mine to change.

### F7 — the grep now reproduces

Ran the printed command verbatim:

```
grep -rl "2026-07-09-ghmem-design" --include="*.md" . \
  | grep -v "^./.git/"
```

Five files, not the claimed nothing and not the three the review saw
(the two review reports have been written since):
`.claude/handoff-task.md`, `plans/unroute-event/classification.md`,
`plans/ghmem-design-migration/report.md`,
`plans/ghmem-design-migration/reports/deliverable-review.md`,
`plans/ghmem-design-migration/reports/deliverable-review-prose.md`.

Replaced with a scoped command that does reproduce:

```
grep -rl "2026-07-09-ghmem-design" \
  --include="*.md" docs tools memory
```

Verified: no output, exit status 1. The report now also states why the
whole-tree hits are benign — `classification.md:66–67` is a
deliberate historical statement, and the rest are the report and the
artifacts
discussing the migration — rather than pretending they do not exist.

The files-patched count is corrected from
`git show f3d1276 --numstat`:
**eleven reference lines across ten files** — ten lines in the nine
files under `docs/superpowers/`
(`2026-07-24-ghmem-agents-design.md` carries two), plus one in
`tools/ghmem/README.md`. Eight carried the
full path, three (`c1-index`, `c2-topic-close`, `d-reentry`) the bare
sibling form; the old list wrongly put `agents` in the bare group.

Two brief-named files were removed from the list, with the reason:
`memory/ghmem-project.md` does name the new path, but that arrived in
the memory submodule's own commit `0188098` (which rewrote the file
whole) and `f3d1276` bumps no gitlink; and neither
`plans/unroute-event/` file was patched.

### F8 — the 72-character claim

`report.md`'s "Line counts" section now opens "Measured on the files
as they landed in `f3d1276`; both have been edited since, so
re-measure rather than quoting these figures forward", and the claim
reads "Prose is hard-wrapped to the repo's ~72-column convention, with
a handful of lines a few columns over and none over 80" — the
convention named, the count not asserted. `docs/ghmem-design.md` was
not measured or opened.

Two further stale claims in the same file, fixed while there: the
header and the closing line both said "Nothing committed", which
`f3d1276` made false. They now name the parent commit and the memory
submodule's `0188098`. The split-seam paragraph's `(373–573)` and
`(574–651)` citations into the design doc were replaced with the
section names plus "ran 280 lines together in `f3d1276`" — same class
of rot as F10, in a file I own.

### F9 — 88 → 90

`wc -l -c memory/ghmem-project.md` → `90 3999`, and
`git -C memory show 0188098:ghmem-project.md | wc -l -c` agrees. The
line count is now 90; the 3,999-byte figure was already exact.

### F10 — line citations replaced with durable pointers

`docs/ghmem-design.md` was **not** re-measured and not opened. Every
line-number citation into it is gone from the table, replaced by the
section plus the heading a reader can search for:

| was | now |
| --- | --- |
| `Architecture §1, lines 155–277` | `Architecture §1` |
| `NFR5; Architecture "**Storage.**" para, lines 264–277` | `NFR5; Architecture, the "**Storage.**" paragraph` |
| `FR3; Design decisions "The routing note arrives on stdin", lines 456–465` | `FR3; Design decisions, "The routing note arrives on stdin"` |
| `NFR3; Design decisions lines 444–455; …` | `NFR3; Design decisions, the containment-hook paragraph; …` |
| `Architecture §3 line 314 and Design decisions line 433` | `Architecture §3, and the Design-decisions paragraph on the agent definitions` |

Why: the numbers were already 1–2 low against the draft they cited,
another agent is rewriting the file as I write this, and a line number
into a doc corrected on every slice is a pointer that rots on the next
commit. Where I had no confirmed heading text I used a descriptive
phrase rather than inventing one — the "Grepped for" column already
gives the reader a literal to search. A note in the file states the
policy so a later editor does not put line numbers back.

`grep -nE 'line[s]? [0-9]{2,}|:[0-9]+–[0-9]+'` over the file now
returns nothing. Code-file citations (`sync_commits.py:39-42` and
friends) were left alone: those files are not being rewritten and F10
was scoped to the design doc.

### F11 — the twelve-command cell

Split into a three-row table above the main one, grouped by pipeline
stage — read commands; the session loop; snapshot, close-out and
withdrawal — each row carrying its own "grepped for" literals and
`Architecture §1`. Widest of the three is 175 columns against the
original row's 285.

## Not applied

- **The other wide table rows in `reduction-report.md`.** The task
  said row 50 "is 304 columns wide"; measured against `HEAD`, row 50
  was **285**. The two genuine 304-column rows are the PR-diffs row
  (now line 72) and a memory-sibling row (now line 95), and 295 and
  283 sit between them. F11 named one row and the convention note
  accepts that table rows cannot be wrapped, so I fixed the row named
  and left the rest. If the intent was "no table row over ~250", say
  so and the same treatment applies to four more.
- **`reduction-report.md`'s 22 prose lines at 73–77 columns.** All
  pre-existing, in paragraphs I did not otherwise touch; rewrapping
  them would bury the substantive diff. Named here rather than done
  silently.
- **`report.md:67`, 75 columns.** Pre-existing, and it is 75 only
  because it carries `docs/ghmem-heat-weights.md` inline.
- **`docs/ghmem-design.md`, `plans/unroute-event/requirements.md`,
  `tools/ghmem/src`, `tools/ghmem/tests`** — not mine. All show as
  modified in `git status`; that is the parallel agents, not me.

## For the human

1. **`f3d1276`'s commit message carries the same miscount.** Its last
   line says "All thirteen inbound references were rewritten". The
   real figure is eleven reference lines across ten files. The commit
   has landed, so the message cannot be corrected without a history
   rewrite; `plans/ghmem-design-migration/report.md` now carries the
   right number, and this is recorded so the discrepancy is not read
   as a later regression.
2. **`reduction-report.md:5`'s "was 117,046" bytes does not match
   git.** `git -C memory show 0188098^:ghmem-project.md | wc -c`
   reports **117,073**, 27 bytes more. Not in the finding list and I
   could not determine which measurement is right — plausibly the
   working tree differed from the parent commit by a `modified:`
   frontmatter bump before the reduction ran. Left at 117,046; the
   3,999 and the 90 are both confirmed against git.
3. **The `unroute` re-triage batch is still pending.** Nothing in this
   pass ran it; the changelog now says so and gives the ledger check.
   The 22 items of `BcbBJeUg` and `MZlH9vbE` remain routed.

## Verification

| file | lines | max cols | prose lines over 72 |
| --- | --- | --- | --- |
| `docs/ghmem-changelog.md` | 115 | 72 | 0 |
| `tools/ghmem/README.md` | 180 | 86 (two pre-existing table rows) | 0 |
| `plans/ghmem-design-migration/report.md` | 193 | 87 (a table row) | 1 (pre-existing, 75) |
| `plans/ghmem-design-migration/reduction-report.md` | 216 | 304 (a table row) | 22 (all pre-existing, 73–77) |

F1 re-check, run again after the edits:

```
grep -c '"unroute"' corpus/routing.jsonl
```

→ `0`. The batch has not run.

F7 re-check:

```
cd /Users/david/code/micro && grep -rl "2026-07-09-ghmem-design" \
  --include="*.md" docs tools memory
```

→ no output, exit 1.

`git status --porcelain` at the end of this pass:

```
M  .claude/handoff-task.md
M  .claude/handoff-todo.md
 M docs/ghmem-changelog.md
 M docs/ghmem-design.md
 M plans/ghmem-design-migration/reduction-report.md
 M plans/ghmem-design-migration/report.md
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

Of these I wrote four: `docs/ghmem-changelog.md`,
`tools/ghmem/README.md`, `plans/ghmem-design-migration/report.md`,
`plans/ghmem-design-migration/reduction-report.md` — plus this report
under the untracked `plans/ghmem-design-migration/reports/`. The two
staged `.claude/handoff-*.md` entries predate this session's work.
Everything else is the parallel agents. Nothing committed, no branch
created, `just green` not run.
