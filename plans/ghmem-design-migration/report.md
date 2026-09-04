# ghmem design migration — report

`git mv docs/superpowers/specs/2026-07-09-ghmem-design.md
docs/ghmem-design.md`, then restructured in place. No stub at the old
path. Landed as `f3d1276`.

## Section mapping

| Source section | Went to |
| --- | --- |
| Problem | Opening paragraph, plus the FRs it motivates |
| Research findings (2026-07-09) | Split — see below |
| Decisions | Design decisions, plus Rejected alternatives |
| Architecture | Architecture §1–§3 |
| Validation | NFR10 |
| Error handling | Architecture §4, "Failure handling" |
| Execution structure | Changelog (2026-07-09 entry); the 400-line cap stayed as NFR9 |
| Non-goals | Split — Scope boundaries and Rejected alternatives |

The FR and NFR sections are new. The source had no requirements
section at all: FR1–FR9 and NFR1–NFR10 are the contract its Problem,
Architecture and Decisions sections implied, stated once so the
decisions below them can carry rationale instead of re-deriving what
the system must do.

**Research findings.** Kept, attached to the claim each grounds: the
corpus scale and the retrievability check (verified on PR #3107) in
FR1; the ~7–10k-call REST estimate against the 5k/hr limit and the
30/min search limit in NFR1; the ~3–4M-token corpus and the triage /
synthesis token estimates in NFR6; the "only about a third of closed
PRs encode a decision" literature warning as its own design decision,
since it is the whole reason `no-signal` is first-class; the GH
Archive coverage window (2018–Oct 2025) in the rejected alternative it
kills; the prior-art survey as the "An existing tool" rejected
alternative. Dropped: the `Date:`/`Status:` header, the "Accepted
blind spots" framing (the blind spots themselves survive as scope
boundaries), and nothing else — every measured figure carried over
with its date.

**Non-goals.** Discord/Gitter, Projects scope, the wiki, hosted
service / embeddings / chatbot, no automatic upstream PR, and "topic
docs are not exhaustive changelogs" are scope boundaries: no
alternative was weighed, they are lines the project drew. The prior-art
survey is the one non-goal-adjacent item that *was* an evaluated
option, so it is a rejected alternative.

## Genuinely ambiguous calls

1. **Validation → NFR10 rather than an Architecture subsection.** The
   six-section shape has no Validation slot. The wrapindent gate is a
   condition the pipeline must satisfy before it is trusted, which
   reads as a non-functional requirement; its distractor rule and its
   "semantic, not textual" comparison rule are preserved whole.
2. **The 400-line cap and the toolchain bar stayed in the living
   doc** (NFR8, NFR9) while the four-subproject sequencing went to the
   changelog. The sequencing is spent — those subprojects have landed.
   The cap and the toolchain still constrain every artifact the
   project produces.
3. **Error handling became "Failure handling" under Architecture.**
   Every entry is a mechanism description in the present tense, which
   is architecture, not rationale.
4. **`docs/superpowers/plans/2026-07-06-popup-layer.md` is not
   indexed.** The directory holds ten plans, but that one belongs to
   micro's popup-layer work, not ghmem. Nine ghmem plans are indexed.
   Say the word if the changelog was meant to index the directory
   rather than the project.
5. **Two non-plan dated records are indexed**: `docs/ghmem-heat-weights.md`
   and `docs/ghmem-tail-sample.md` (both 2026-07-25). They are the
   calibration record NFR7 rests on, and heat.py's own docstring
   points at the first.
6. **Uneven spec/plan pairing.** B1 (2026-07-21) has a plan and no
   spec; C1 and the agents subproject have a spec and no plan. Each is
   indexed by whatever exists.

## Moved to the changelog

- The four-subproject execution structure and its just-in-time,
  contract-style planning discipline. Implementation history: the
  subprojects have landed, so in the living doc it is a schedule
  describing the past.
- The `unroute` batch's own particulars — the 22 items, the two
  session tokens, the 95-routes/71-topics/57-singletons corpus
  measurement — written into the 2026-09-02 entry. The living doc
  carries the event's *design* (what `unroute` deletes and why, the
  emptied-topic rule, the three rejected alternatives around it); the
  batch that motivated it is a dated fact.

## Stale against the current code

Everything below was corrected to current truth in the living doc.
Code read under `tools/ghmem/src/ghmem/`.

1. **`ghmem triage --requeue` does not exist.** Reclaim is its own
   command, `ghmem requeue` (`cli.py:73`).
2. **`ghmem rebuild` is missing from the spec entirely.** It replays
   both ledgers into SQLite and appends a `reenter` event per route
   the corpus has moved past (`cli.py:47`). The incremental loop is
   now `sync` → `rebuild` → `requeue` → triage → synthesize.
3. **The routing note is not a positional argument.** `route` takes
   `<token> <number> <verdict>` and reads the note from stdin, fed by
   a quoted heredoc (`cli.py:245`, `cli.py:273`). The spec's
   "`no-signal` takes none" is inverted: every verdict requires a
   note, `no-signal` included (`triage.py:285–319`).
4. **The snapshot token binds a content digest, not a
   ledger-sequence/`updated_at` high-water mark.** `topic.assemble`
   records `{token, topic, digest}` and `close_topic` compares the
   digest over the topic's routed set
   (`topic.py:59`, `dispatch_store.py:235`).
5. **The containment hook is a positive character-class allowlist over
   the whole command string**, not a list of permitted `ghmem`
   subcommands, with one quoted-heredoc shape permitted through for
   the note (`tools/ghmem/hooks/scripts/ghmem-only.sh`). A second
   hook, `scope-docs-design.sh`, scopes the synthesize agent's Write
   and Edit calls.
6. **`ghmem heat` emits CSV** (`cli.py:153`); the spec described the
   ranking without its output contract.
7. **`ghmem index` lines cap citations at five, heat-ordered**
   (`index_cmd.py`). Alphabetical topic ordering held.
8. **The heat weights are calibrated and retained, not pending.** The
   2026-07-25 fit produced no better vector and the fitted one lost
   under cross-validation (`heat.py`, `docs/ghmem-heat-weights.md`).
   The spec's "provisional, calibrated in subproject 4" now reads as
   the standing rule plus the result.
9. **Commands take issue/PR numbers, not ids** — `show <number>`,
   `route <token> <number> …`.
10. **Corpus paths are named**: `corpus/fetch.jsonl`,
    `corpus/routing.jsonl`, `corpus/cache.sqlite`
    (`paths.py`), which the spec left implicit.

Noted but not surfaced in the doc: the routing reducer lives in
`routing_reduce.py`, not `ingest.py`. The living doc names no reducer
module, so there was nothing to correct — it says only that the
reducer writes SQLite and is replayed.

## Line counts

Measured on the files as they landed in `f3d1276`; both have been
edited since, so re-measure rather than quoting these figures forward.

- `docs/ghmem-design.md` — **656 lines**, against the 400-line soft
  reviewability budget. Reported rather than shaved: the overage is
  content, not padding. Prose is hard-wrapped to the repo's ~72-column
  convention, with a handful of lines a few columns over and none over
  80.
- `docs/ghmem-changelog.md` — 123 lines.

The source was 517 lines with no FR/NFR sections and no coverage of
`rebuild`, `requeue`, `unroute`, the reenter path, the hook's real
shape, the heredoc note protocol or the calibration result. The
cleanest split seam, if one is wanted, is the two rationale sections —
"Design decisions" and "Rejected alternatives", which ran 280 lines
together in `f3d1276` and would have left a ~380-line doc of
requirements and architecture pointing at them. That is a call for my
human partner, not one to take unasked: the whole point of the
rationale sections is that they sit beside what they justify.

## Grep verification

```
grep -rl "2026-07-09-ghmem-design" \
  --include="*.md" docs tools memory
```

Returns nothing, exit status 1: no doc, no tool file and no memory
file still names the old path.

Widening the search to the whole tree does return hits, and every one
is benign — the search is scoped above precisely because they are.
`plans/unroute-event/classification.md:66–67` names the old path in a
deliberate historical statement ("was X … and has since been migrated
to Y"), and the rest are this report plus the artifacts that discuss
the migration (the deliverable-review reports, the session's
`.claude/handoff-task.md`). None is a reference a reader would try to
follow.

Eleven reference lines were patched, across ten files: ten lines in
the nine files under `docs/superpowers/`
(`2026-07-24-ghmem-agents-design.md` carries two of them), plus one in
`tools/ghmem/README.md`. Eight carried the full path and three
(`c1-index`, `c2-topic-close`, `d-reentry` specs) carried a bare
`2026-07-09-ghmem-design.md` sibling reference; both forms were
rewritten to `docs/ghmem-design.md`. `tools/ghmem/README.md`, as a
living consumer, had its sentence rewrapped around the shorter path;
the frozen records got the path swap only.

Two files named in the brief were *not* patched here.
`memory/ghmem-project.md` does name the new path, but that arrived in
the memory submodule's own commit `0188098`, which rewrote the file
whole; `f3d1276` bumps no gitlink. Both `plans/unroute-event/` files
were left alone, `classification.md` deliberately so.

The parent commit is `f3d1276`; the memory submodule's own reduction
of `ghmem-project.md` is `0188098` there.
