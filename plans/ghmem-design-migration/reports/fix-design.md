# Fix report: docs/ghmem-design.md

All findings in Groups A, B, C and D were verified against the code
before the sentence was rewritten. Every fix was applied. Two judgement
calls I could not make from the code alone are flagged under "Needs a
decision".

## Applied

### Group A — false statements about the CLI

**A1 — `ghmem status` "stuck" count.** Verified against
`tools/ghmem/src/ghmem/status.py:68-78` (`render_status`) and
`grep -rn "stuck" tools/ghmem/src` → no matches. The emitted line is
`total= triaged= routed= no_signal= pending= dispatched=`.

- Old: "coverage summary: counts of triaged, routed, no-signal,
  pending, dispatched and stuck items, the full list of known topics
  …, the dirty subset, and the sync cursor position."
- New: "coverage summary: one counts line carrying `total`, `triaged`,
  `routed`, `no_signal`, `pending` and `dispatched`, where
  `dispatched` is the stranded set — dispatched but not yet routed —
  that `requeue` reclaims. Then the full list of known topics (so
  triage can consult it before minting a near-duplicate), the dirty
  subset, and each connection's sync cursor position."

`total` is now named, the invented `stuck` is gone, and the doc says
what `dispatched` actually counts (`dispatched_unrouted()`, surfaced
in `StatusSummary`). "each connection's sync cursor" matches
`_last_cursor_per_connection` / the `cursor <connection>: <hwm>` lines.

**A2 — the `fit` / `diff_cut` / `oversized` tag is ledger-only.**
Verified against `triage.py:129-147` (the `fit` path passes no note),
`triage.py:210-215` (`_render_packet` emits `packet-note:` only when
`note is not None`) and `triage.py:218-221`
(`_DIFF_CUT_NOTE = "diff truncated to fit the batch budget"`,
`_OVERSIZED_NOTE = "served without its diff body; thread text and file
list leave no budget for it"`).

- Old: "Every dispatch is tagged with which of the three happened —
  `fit`, `diff_cut`, `oversized` — in the packet and in the ledger, so
  the budget can be calibrated against that tag."
- New: "Every dispatch records which of the three happened — `fit`,
  `diff_cut`, `oversized` — in its **ledger event**, so the budget can
  be calibrated against that tag. Those tokens are ledger vocabulary
  and do not reach the packet: a truncated packet carries a prose
  `packet-note:` line naming what was cut, and a packet that fit
  carries no note at all."

**A3 — FR2 / NFR3's containment claim.** Verified by reading
`tools/ghmem/hooks/scripts/ghmem-only.sh` in full: the guard is
`bare_re="^ghmem(${sep}${arg})*$"` plus a quoted-heredoc opener, i.e. a
positive character class over the whole command string with no
subcommand vocabulary at all. Also verified the credential half:
`github.py:70` takes `os.environ.get("GITHUB_TOKEN") or
_gh_auth_token()`, so nothing hands a spawned agent a token — but
`ghmem sync` run *by* an agent would acquire one from the ambient
environment, and the hook admits it.

- Old FR2: "`show`, `discussion`, `heat`, `status` and `topic` render
  from the local store. No agent holds a GitHub credential or issues
  an API call."
- New FR2: "… render from the local store, and an agent's whole
  procedure is written against them. Nothing hands a spawned agent a
  credential of its own. That it issues no API call is a property of
  its prompt, not of the containment hook — see Limitations."

- Old NFR3: "the agents' **data path** is mechanically contained. A
  PreToolUse allowlist hook … permits `ghmem` invocations and denies
  everything else, so containment does not rest on the agent following
  its prompt."
- New NFR3: "the agents' **command surface** is mechanically
  contained. A PreToolUse allowlist hook on each agent definition
  admits only `ghmem` invocations, with no chaining, substitution,
  pipes or redirects, and denies every other command with a structured
  reason — so what an agent may *run* does not rest on it following
  its prompt. The hook reads command shape, so which `ghmem`
  subcommand runs is outside what it decides."

The trade-off itself is now a Limitation ("Containment is
command-shape, not subcommand"), which also reconciles the doc with
its own rejected alternative at the old `:601-612`.

**A4 — FR3's note-reappears claim.** Verified against
`dispatch_store.py:264` and `:352`, both of which carry
`.where(Route.verdict == name, Route.verdict != NO_SIGNAL)`, and
`topic.py:95-118`, whose docstring states the consequence outright.

- Old: "or the reserved verdict `no-signal`, which also carries a note.
  The note reappears in the item index `ghmem topic` emits."
- New: "… which also carries a note. A topic verdict's note reappears
  in the item index `ghmem topic` emits. A `no-signal` note appears in
  no rendered output at all, since `routed_items` excludes
  `no-signal`; it lives on its ledger event and its `Route` row."

The unrendered `no-signal` note is also recorded as a Limitation.

**A5 — the two PR counts.** Re-measured with a read-only query against
`corpus/cache.sqlite` (`python3`/`sqlite3` — there is no `sqlite3`
binary on this box): `issue 2575`, `pull_request 1302`, `discussion
245`, `commit 3529`, commit dates `2016-03-11` → `2026-07-14`;
`issue OPEN 866`, `pull_request MERGED 899`, `pull_request CLOSED 237`.

- Old FR1: "Scale, verified against the live repo (2026-07-09): 2,569
  issues (862 open), 1,245 PRs (864 merged, 212 closed-unmerged),
  3,524 commits over 10.3 years, 243 Discussions."
- New FR1: "Scale, read off `corpus/cache.sqlite` on 2026-09-03: 2,575
  issues (866 open), 1,302 PRs (899 merged, 237 closed unmerged),
  3,529 commits spanning 2016-03 to 2026-07, and 245 discussions.
  Those figures grow with every `sync` and are here for order of
  magnitude; the corpus is the live source, and every other corpus
  count in this document carries the date it was measured."

- Old diff paragraph: "Measured (2026-07-10, live API): … only 10 of
  1,299 PRs exceed 2k changed lines".
- New: "Measured 2026-07-10 against the live API, when the corpus held
  1,299 PRs: … only 10 PRs exceed 2k changed lines".

The two are now reconciled by stating what each is stamped to and by
saying explicitly that the corpus is the live source. I kept a stamped
figure rather than switching to a live one — see "Needs a decision".

**A6 — the `ghmem topic` field list.** Verified against
`topic.py:104-118`, which renders title, state, opened, last active,
author-with-standing, then `comments`, `reactions`, `participants`,
`related`, an optional diffstat, then `note:`.

- Old: "(title, state, dates, author with standing, participant and
  reaction counts, cross-references; for PRs the diffstat)"
- New: "(title, state, dates, author with standing, comment, reaction,
  participant and related-item counts; for PRs the diffstat)"

**A7 — a note is a single line.** Verified against `cli.py:279-287`
(`_read_note`): `strip()`, then `TriageSessionError.empty_note()` on
empty and `TriageSessionError.multiline_note()` on any `"\n"`.

- Old (`route` bullet): "The note arrives on stdin, and only on stdin."
- New: "The note arrives on stdin, and only on stdin, as a **single
  line**: `route` strips it and refuses one that is empty or carries a
  newline."

Also recorded as a Limitation ("A routing note is a single line"), so
a writer meets it before reaching the command reference.

### Group B — requirements that assert what the system does not have

**B1 — the emptied-topic invariant.** Verified against
`routing_reduce.py`: `_release_topic` (`:236`) is called only from
`_apply_unroute_event` (`:233`); `_apply_route_event` (`:125-141`)
calls `_mark_topic_dirty` on both the left and the joined topic and
never releases. Corpus check: 0 empty `Topic` rows across 95 routes.

- Old: "**Unrouting a topic's last item deletes the `Topic` row.** A
  topic row exists exactly while some route names it, and an emptied
  one would sit in `status`'s dirty set as a name `ghmem topic` then
  refuses."
- New: "**Unrouting a topic's last item deletes the `Topic` row.** An
  emptied topic would otherwise sit in `status`'s dirty set as a name
  `ghmem topic` then refuses with `TopicNotRoutedError`, stalling the
  synthesis loop … The invariant "a `Topic` row exists exactly while
  some route names it" therefore holds on the `unroute` path alone —
  re-routing a topic's last item still leaves an empty dirty row, and
  that state is reachable (Open questions)."

The unresolved half is an Open question with both sides stated and
neither settled, including the five tests that pin the current `route`
behaviour and the zero-empty-rows measurement.

**B2 — NFR7's calibration claim.** Verified: `standing.py:20-22` still
carries `# PROVISIONAL — calibrated in subproject 4` on `OWNER_LOGIN`,
`MERGE_THRESHOLD` and `ACCEPTED_PR_THRESHOLD`; `budget.py:17-18` on
`BATCH_ITEMS = 12` and `BATCH_CHARS = 200_000`; `show_diff.py:19` on
`DIFF_BODY_MAX_CHARS = 25_000`. `heat.py:7-12` is the only calibrated
family and says "retained".

- Old: "**NFR7 — heuristic constants are calibrated, never hip-shot.**
  Heat weights and standing thresholds ship as provisional values and
  are derived by a recorded analysis over the real corpus."
- New: "**NFR7 — heuristic constants are grounded in data, never
  hip-shot.** Each weight and threshold ships provisional and stays
  provisional until an analysis over the real corpus is recorded
  against it. One family has had that analysis: the heat weights,
  calibrated 2026-07-25 against 198 labels and **retained**, because
  no fitted vector ranked better and the fitted one lost under
  cross-validation (`docs/ghmem-heat-weights.md`). Three families have
  not; they are listed in Open questions."

"Retained" replaces "derived by", which is the true claim and removes
the contradiction with the calibration decision below. The three
unbacked families are named with their values and `PROVISIONAL` status
in Open questions, so the open item is not retired silently.

**B3 — NFR9.** Restated as a soft budget with a stated bar.

- Old: "**NFR9 — 400 lines is the cap on any human-reviewed
  artifact**, plans and PRs alike; anything longer is split. Sole
  exception: PRs that merely reorganize code with no behavioral
  change."
- New: "**NFR9 — 400 lines is the reader-context budget for a
  human-reviewed artifact**, plans and PRs alike. A budget, not a hard
  cap: a split has its own cost — re-reading several documents,
  chasing cross-references — so a cohesive document may run over when
  the split would cost its reader more than the length does, provided
  the overage is stated rather than absorbed. A PR that merely
  reorganizes code with no behavioral change is exempt outright. This
  document is over budget and deliberately whole: the natural seam
  would separate a decision from the requirement it justifies, leaving
  two documents whose copies can drift apart."

The document now discloses its own overage, which is what the restated
requirement demands of any artifact.

**B4 — NFR10's unrun gate.** Verified: `ls docs/design/` holds
`INDEX.md`, `autocompletion.md`, `clipboard-behavior.md`,
`debian-packaging.md`, `file-tree-sidebar.md`, `lsp-support.md` — no
wrap topic; `docs/ghmem-changelog.md` has no subproject-4 / validation
slice record; the corpus holds 95 routes.

- Old: ended "… `heat`/`triage`/`route`/`status` are pure functions
  over the local store, testable without network."
- New: ends "… testable without network. **This gate has not run**, so
  the requirement stands unmet; see Open questions."

The Open question carries the evidence (no wrap doc, no changelog
slice, 95 routed and five docs synthesized) and names the two ways out.

### Group D — minor

- **FR7 title.** Old "a verdict can be corrected in three distinct
  ways" listing four mechanisms → new "**FR7 — a verdict is
  correctable, and a stranded dispatch is reclaimable.** Three
  mechanisms correct a verdict. … `ghmem requeue` is not a verdict
  correction — it frees items a crashed session dispatched but never
  routed."
- **NFR8.** Rewritten around one testable bar — "the toolchain gate is
  the bar for every change… a change lands only when it passes" — with
  the toolchain choices carried as "the settled choices under it"
  rather than as ten requirements wearing one number.
- **FR3's end state.** Old "triage assigns every item a verdict" → new
  "triage assigns **each item it serves** a verdict", closing with
  "Coverage of the whole corpus is FR9's end state, not a property
  that holds today: 95 of the corpus's 3,877 issues and PRs carry a
  verdict as of 2026-09-03." (2,575 + 1,302 = 3,877, re-derived.)
- **FR4 / `INDEX.md`.** Verified `scope-docs-design.sh` resolves
  `$project_dir/docs/design` and passes anything under it, so
  `INDEX.md` is inside the permitted tree; the prohibition is
  `.claude/agents/ghmem-synthesize.md:84-85` ("You do not touch
  `INDEX.md` yourself"). Old "is regenerated from those docs and is
  never agent-edited" → new "is regenerated by `ghmem index` from
  those docs. The synthesize agent is told not to edit it, which is a
  prompt rule and not a hook guarantee — see Limitations." The trailing
  "INDEX.md is harness-generated, never agent-edited" on the `ghmem
  index` architecture bullet was dropped as the same overclaim.
- **FR5's "structural guard"** (a Minor in the review, same class as
  A3/FR4). Old "This is the structural guard against hallucinated
  rationale" → new "It is the standing rule against hallucinated
  rationale, held up by the synthesize agent's prompt and by review
  rather than by the harness."

## Structure

Two new sections sit between the NFRs and Architecture, so the
document's own order matches the FR / NFR / Limitations / Open
questions / History structure its Architecture §2 mandates — the
Changelog section at the end is the History analogue.

**Limitations (33 lines, 9 bullets)** — accepted trade-offs, matching
§2's definition and *not* a graveyard of rejections (the Rejected
alternatives section stays where it is).

1. No hosted service, embeddings or chatbot.
2. No Discord / Gitter / mailing-list mining.
3. GitHub Projects scope gap and the unchecked wiki.
4. No automatic upstream PR.
5. Topic docs are not exhaustive changelogs.
6. **Containment is command-shape, not subcommand** (from A3), with
   the reason the cost was accepted.
7. **`INDEX.md` is protected by prompt, not by hook** (from D4).
8. **A routing note is a single line** (from A7).
9. **A `no-signal` note is never rendered** (from A4).

Bullets 1-5 are the former "Scope boundaries" subsection under FR,
moved verbatim: they were already Limitations by §2's own definition
and had no business inside the requirements section.

**Open questions (44 lines, 5 bullets)** — live debates with their
current front line.

1. **Should `route` converge on `unroute`'s emptied-topic behaviour?**
   (B1) Argument each way, the five pinning tests named, the
   zero-empty-rows measurement stamped, no verdict taken.
2. **NFR10's ground-truth gate has not run.** (B4)
3. **Three constant families are still provisional.** (B2) Named with
   their values.
4. **The `topic merge` event** — the topic-name-to-canonical mapping
   replayed by `rebuild`, why it is the next lever (57 of 71 topics
   hold one item), and why the per-serve topic list narrows but cannot
   close the problem (it does not re-judge names already minted).
   Sourced from `plans/unroute-event/requirements.md:199-205`.
5. **The triage rubric's no-signal rate** — 4 of 95 routes against the
   36.5% calibrated base rate. This is the "anything else you find"
   item: it was recorded as out of scope in the unroute requirements
   and had no home in the living doc.

## Deduplication

**Line count: 656 → 712.** The document grew, and the honest
accounting is that deduplication paid for the corrections but not for
the new sections:

| cause | net lines |
| --- | --- |
| Limitations + Open questions (less the 13-line Scope boundaries subsection they absorbed) | +62 |
| Corrections in Groups A, B and D (mostly additive — an unmet requirement takes more words than a false one) | ≈ +54 |
| Deduplication | ≈ −60 |

Cut, by the C1 table:

- **FR9** lost "so the run is abortable at any point with the best
  material banked"; the "Heat-ranked backfill order" decision keeps it
  and now leads with it.
- **NFR2** kept as the contract; the "12-factor split" decision was
  reordered to lead with its rationale instead of re-listing the
  harness's responsibilities.
- **NFR3** kept as the contract; the "Triage and synthesize are custom
  agents" decision lost its allowlist restatement and now carries only
  the custom-agent rationale (file-watched, frontmatter `hooks`),
  since "The allowlist is a positive character class" owns the
  mechanism.
- **NFR4** kept as the contract; "Triage agents pull their own items"
  lost its restatement of the session loop and the CLI-side metering
  (both already in NFR4 and in the `triage`/`route` bullets) and keeps
  the cost analysis, which is the part that cannot be reconstructed.
- **NFR7** kept as the contract; "Formula constants are calibrated"
  was retitled "Calibration normalizes before it weights" and now
  carries only the normalization analysis and the ground-truth checks.

Cut elsewhere:

- **C2** — FR4 no longer spells out the topic-doc section structure; it
  points at Architecture §2.
- **FR7's `unroute` restatements** — the `ghmem unroute` architecture
  bullet lost the append-only sentence (NFR5 owns it); the
  "Freshness is stamped on the route" decision lost its re-listing of
  what `reenter` and `unroute` each delete (FR7 owns it). The four
  Rejected-alternatives entries were left untouched per C3.
- **Architecture §4** — three of six failure bullets were verbatim
  restatements of the `route`, `close`, `status` and `requeue` command
  contracts above them; they collapse into one sentence pointing at
  those commands. The three sync-side bullets stay.
- **Architecture §3** — the triage agent's bullet no longer re-walks
  the `triage`/`route` loop; the synthesize agent's bullet no longer
  restates FR5 and FR8's standing rules in full.
- **The "Crash recovery is explicit, not timed" decision** was cut
  outright: its rationale was already in §4 *and* in the "Dispatch
  timeouts for crash recovery" rejected alternative, in the same
  words.
- **"Index entries are retrieval hooks, not titles"** folded into "Doc
  form: hybrid, native-memory shaped", which was already describing
  `INDEX.md`.
- **The two diff-cutting decisions** were re-cut along their real seam:
  "PR diffs are corpus data" keeps why diffs are corpus data plus the
  measurement, and the second decision (now "A diff body is the only
  cuttable part of a packet") owns all the cutting mechanics, which
  were previously split across both.
- **The stdin-note decision** folded into the allowlist decision's
  wake — it no longer re-explains that a quoted heredoc expands
  nothing, which the allowlist decision establishes three paragraphs
  earlier.
- Smaller: the `sync` bullet's restatement of FR6, the `rebuild`
  bullet's restatement of FR7's freshness comparison, the `show`
  bullet's restatement of FR8, the storage paragraph's restatement of
  NFR5, the standing decision's restatement of FR8's rendering rule.

**I stopped at roughly 60 lines rather than the 100-150 the review
projected.** Reaching that range would have meant either deleting the
five requirements outright rather than cutting them back to their
contract, or cutting rationale — which the brief forbids and which is
the part a later session cannot reconstruct. Every remaining long
passage I checked (the allowlist decision, the batch-budget cost
analysis, the calibration normalization, `ghmem topic`/`close`, the
storage model, Architecture §2) states something once. Section sizes
now: FR 67, NFR 88, Limitations 33, Open questions 44, Architecture
205, Design decisions 179, Rejected alternatives 78, Changelog 5.

## Not applied

**Nothing from the brief was skipped.** Two calls I made that a
different reading would have made differently:

- **FR1 stays a stamped snapshot rather than becoming a live figure.**
  The doc is prose, not a template, so there is nothing to interpolate
  a live count into; the honest form is a stamped figure plus an
  explicit statement that the corpus is the live source and that the
  figures grow. That is what I wrote. If the intent was to drop the
  numbers entirely and point at `ghmem status`, that is a one-line
  change I did not take on my own.
- **The `ghmem heat` "CSV because" aside and the `ghmem index`
  "alphabetical because" aside stay in Architecture** rather than
  moving to Design decisions. Both are one-clause justifications
  attached to the thing they justify, appear once, and moving them
  would add cross-references without removing lines.

### Needs a decision

- **B1's convergence question is recorded, not settled** — as
  instructed. The Open question states both sides and names the five
  tests that would have to be overturned. Nothing in the document now
  implies the invariant is global.
- **NFR10 is now stated as unmet.** The alternative reading — that the
  gate is obsolete because backfill has proceeded without it, and NFR10
  should be rewritten to describe what is actually relied on — is
  named inside the Open question but not chosen. Choosing it means
  deciding whether the pipeline's outputs are trusted, which is not a
  call the code can settle.

## Verification

```
$ wc -l docs/ghmem-design.md
712 docs/ghmem-design.md

$ awk '{if(length($0)>72)c++} END{print c+0}' docs/ghmem-design.md
3

$ python3 -c "…"     # character-aware, not byte-aware
max chars 72   over72 chars 0
max bytes 76   over72 bytes 3
```

**Width.** 0 lines exceed 72 *characters*; max character width is
exactly 72. The 3 lines `awk` reports are byte-length artifacts of
multibyte typography (`—`, `→`, `–`, `≈`, `×`) — all three (`:114`,
`:428`, `:618`) are in paragraphs this pass did not touch. The
baseline was 4 such lines with a max of 76 bytes; the fourth was
inside a rewritten paragraph and is gone. Every paragraph I rewrote or
reflowed was wrapped byte-aware at 72, so none contributes.

**Rejected alternatives intact.** 13 entries, unchanged:

```
$ sed -n '/^## Rejected alternatives/,/^## Changelog/p' docs/ghmem-design.md | grep -c '^\*\*'
13
```

An existing tool · Shelling out to `gh api graphql` · A companion
procedure skill · A deny-pattern hook · Blocking `ghmem unroute` in the
containment hook · A `--session` selector on `unroute` · Refusing to
unroute a `no-signal` verdict · Relaxing `record_verdict` · Dispatch
timeouts for crash recovery · Digest-only tiers · Delta machinery · GH
Archive for historical `author_association` · Heat-ordered `INDEX.md`.

**No draft archaeology.** No sentence refers to an earlier version of
this document; the corrections state the surviving design positively.

**Files touched.** `git status --porcelain` at the end of this run:

```
M  .claude/handoff-task.md          (pre-existing, staged before I started)
M  .claude/handoff-todo.md          (pre-existing, staged before I started)
 M docs/ghmem-design.md             ← mine
 M docs/ghmem-changelog.md          (another agent)
 M plans/ghmem-design-migration/reduction-report.md   (another agent)
 M plans/ghmem-design-migration/report.md             (another agent)
 M plans/unroute-event/requirements.md                (another agent)
 M tools/ghmem/README.md                              (another agent)
 M tools/ghmem/src/ghmem/*.py  (7 files)              (another agent)
 M tools/ghmem/tests/test_cli_unroute.py              (another agent)
 M tools/ghmem/tests/test_unroute.py                  (another agent)
?? plans/ghmem-design-migration/reports/   ← contains this report
?? plans/unroute-event/reports/            (another agent)
```

`git diff --numstat docs/ghmem-design.md` → `313 257`. I edited only
`docs/ghmem-design.md` and this report. Nothing committed, no branch
created, `just green` not run.

**Code read for verification** (read-only): `status.py`, `triage.py`,
`cli.py`, `dispatch_store.py`, `topic.py`, `routing_reduce.py`,
`standing.py`, `budget.py`, `heat.py`, `show_diff.py`, `github.py`,
`tools/ghmem/hooks/scripts/ghmem-only.sh`,
`tools/ghmem/hooks/scripts/scope-docs-design.sh`,
`.claude/agents/ghmem-triage.md`, `.claude/agents/ghmem-synthesize.md`,
plus read-only queries against `corpus/cache.sqlite`.
