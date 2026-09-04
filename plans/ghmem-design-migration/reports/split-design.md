# Split report: docs/ghmem-design.md

712 lines became a 399-line entry point plus three files it names.
Nothing was summarized, nothing dropped; 22 pointers were added and
NFR9 was rewritten.

## When-needed analysis

Each block judged by one question: **what question sends a reader
here, and do they have it on every read?**

| Block | The question that sends a reader here | How often | Side |
|---|---|---|---|
| Intro (1–13) | What is this system for? | Every read — it is what makes the rest parseable | **Always** |
| FR1–FR9 (14–80) | What must it do? | Every read; the contract every other block serves | **Always** |
| NFR1–NFR10 (81–168) | What constraints bind it? | Every read; NFR3/NFR8 are the ones a change is checked against | **Always** |
| Limitations (169–201) | What does it deliberately not do? | Every read — it is what stops a reader proposing a gap-closing feature that was never wanted | **Always** |
| Open questions (202–245) | What is still unsettled? | Every read — the brief's "no branch settles one quietly" only works if this loads with the doc | **Always** |
| Architecture preamble + §1 stack line (246–252) | How do the pieces fit? Which stack? | Every read | **Always** |
| §1 Harness — per-command bullets (254–351) | What exactly does `ghmem triage` emit? What is `route`'s stdin rule? What exit code on an empty batch? | Only when working on that command | **Triggered** |
| §1 Storage (353–368) | Which ledger holds what, and what is regenerable? | Only when touching the store or a ledger event | **Triggered** |
| §2 Artifact (370–400) | What does the system produce? | Every read — the output artifact is half the system's purpose, and FR4 defers its body structure to it | **Always** |
| §3 Agentic layer (401–435) | Who judges, at what tier, in what loop? | Every read — the harness/agent boundary is the design's spine | **Always** |
| §4 Failure handling (436–449) | What happens when a fetch or a session dies? | Every read; 14 lines, and it is where the reclaim-not-timeout stance lives | **Always** |
| Design decisions (451–629) | *Why* this mechanism and not another? | Only when about to change one | **Triggered** |
| Rejected alternatives (630–707) | Was X already ruled out? | Only when about to propose something | **Triggered** |
| Changelog pointer (708–712) | Where is the dated record? | Every read | **Always** |

Three judgements worth stating, because they cut against block size:

- **§4 Failure handling stayed** though it is short and reads like
  detail. Its content is a stance — explicit reclaim, never a timeout
  — that a reader must hold to understand why `requeue` exists at all.
  Its one genuine rationale item (**Dispatch timeouts for crash
  recovery**) moved and is pointed at.
- **§2 Artifact stayed** though at 31 lines it is the largest
  always-side block that could plausibly have moved. It describes what
  the system produces, and FR4 is unreadable without it ("the body
  structure Architecture §2 defines"). Moving it would have made the
  entry point describe a pipeline with no stated output.
- **§1's command bullets moved** though they sit inside a section
  everything cites. The section header, the stack line and the
  harness/agent boundary are the orienting part; the per-command
  contract is consulted on a specific question. This is the cut the
  brief anticipated *inside* Architecture, and it is what buys the
  entry point its remaining 115 lines.

## The result

| Path | Lines | Holds | Why a reader opens it |
|---|---|---|---|
| `docs/ghmem-design.md` | 399 | Intro, FR1–FR9, NFR1–NFR10, Limitations, Open questions, Architecture §1–§4, Changelog pointer | The one entry point: what the system must do, must not do, and how it fits |
| `docs/ghmem-harness.md` | 127 | Architecture §1 in full — §Commands (14 command bullets) and **Storage** | To change what a command emits, accepts or records, or to touch the ledgers |
| `docs/ghmem-decisions.md` | 186 | §Design decisions, all 20 entries | To find out why a mechanism has the shape it has, before changing it |
| `docs/ghmem-rejected.md` | 85 | §Rejected alternatives, all 13 entries | To check whether a proposal was already refused, and on what grounds |

Naming follows the existing `docs/ghmem-<something>.md` siblings
(`ghmem-changelog.md`, `ghmem-heat-weights.md`).

## Cross-references

**22 pointers**, each naming a section title and its file, never a
line number. All checked programmatically: every bold title cited
resolves to exactly one section lead in the file named alongside it.

From `docs/ghmem-design.md`:

| Site | Points at |
|---|---|
| Intro | the three detail files; **Doc form: hybrid, native-memory shaped** as the reason for the shape |
| FR1 | **PR diffs are corpus data** |
| FR2 | **12-factor split** |
| FR3 | **A note is prose, so it travels on stdin alone**, **Only about a third of closed PRs cleanly encode a design decision** |
| FR4 | **Doc form: hybrid, native-memory shaped**, **Topic-doc structure** |
| FR6 | **Incremental synthesis reloads full topic context**; refused **Delta machinery for incremental synthesis** |
| FR7 | **Freshness is stamped on the route**, **Unrouting a topic's last item deletes the `Topic` row**; refused **Refusing to unroute a `no-signal` verdict**, **Relaxing `record_verdict` to accept a live re-route** |
| FR8 | **Contributor standing is computed, time-varying data**, **`author_association` is a hint, never authority**; refused **GH Archive for historical `author_association`** |
| FR9 | **Heat-ranked backfill order** |
| NFR2 | **12-factor split** |
| NFR3 | **The allowlist is a positive character class over the whole command string**; refused **A deny-pattern hook**, **Blocking `ghmem unroute` in the containment hook** |
| NFR4 | **Triage agents pull their own items; the CLI meters the batch**, **A diff body is the only cuttable part of a packet** |
| NFR6 | refused **Digest-only tiers in the synthesis context** |
| NFR7 | **Calibration normalizes before it weights** |
| NFR8 | refused **Shelling out to `gh api graphql`** |
| NFR9 | the three detail files by name |
| Limitations, "Containment is command-shape" | **Blocking `ghmem unroute` in the containment hook** (this replaced a bare "see Rejected alternatives", which no longer resolved) |
| Open questions, `route`/`unroute` convergence | **Unrouting a topic's last item deletes the `Topic` row** |
| Architecture §1 | §Commands and **Storage** in `docs/ghmem-harness.md` |
| Architecture §2 | **Doc form…**, **Naming: `docs/design/`, flat, "topic doc"**, **Topic-doc structure**; refused **Heat-ordered `INDEX.md`** |
| Architecture §3 | **Model tiering**, **Triage and synthesize are custom agents, not a skill and a prompt**, **One topic per synthesis spawn, no batching**; refused **A companion procedure skill for the agents** |
| Architecture §4 | refused **Dispatch timeouts for crash recovery** |

From `docs/ghmem-harness.md`: the `unroute` bullet points at refused
**A `--session` selector on `unroute`**.

Each detail file's header points back at `docs/ghmem-design.md` and
across at its siblings.

**Coverage.** All 20 design decisions have an inbound pointer. 12 of
13 rejected alternatives do. The one without is **An existing tool** —
it justifies the project existing rather than any requirement, so
there is no contract for it to hang off; it is reachable from
`docs/ghmem-rejected.md`'s own header, which is the file a reader
opens before proposing anything.

Verification command and result:

```
$ python3 <extract section titles from the two detail files;
           for every "**Title** … `docs/ghmem-<f>.md`" pointer in
           ghmem-design.md and ghmem-harness.md, require exactly one
           matching section lead in the file named>
decision titles 20, rejected titles 13
pointers checked: 22  unresolved: 0
```

## NFR9

**Old text** (final three sentences of the requirement):

> A PR that merely reorganizes code with no behavioral change is
> exempt outright. This document is over budget and deliberately
> whole: the natural seam would separate a decision from the
> requirement it justifies, leaving two documents whose copies can
> drift apart.

**New text** (same position; the budget statement above it is
unchanged):

> A PR that merely reorganizes code with no behavioral change is
> exempt outright. This document holds to the budget as an index and
> names its detail: the command contracts in `docs/ghmem-harness.md`,
> the argument behind each mechanism in `docs/ghmem-decisions.md`, the
> refused designs in `docs/ghmem-rejected.md`. Separating a decision
> from the requirement it justifies risks the two drifting apart, so
> the reference runs one way and always resolves: a requirement names
> the decision behind it by section title, no decision restates the
> requirement it serves, and there is one copy of each to change.

The drift risk named in the old text is not denied — it is answered by
a one-way reference with no duplicated copy to fall out of step. No
narration of the change.

## Verification

### No content lost

Word-token multiset diff between the original file and the
concatenation of the four new files. This is wrap-insensitive and
order-insensitive, so re-wrapped paragraphs and moved sections do not
register; only genuinely absent text does.

```
$ python3 -c "
import collections
orig=open('/tmp/claude-1000/ghmem-design.orig.md',encoding='utf-8').read()
new='\n'.join(open(f,encoding='utf-8').read() for f in
 ['docs/ghmem-design.md','docs/ghmem-harness.md',
  'docs/ghmem-decisions.md','docs/ghmem-rejected.md'])
ca,cb=collections.Counter(orig.split()),collections.Counter(new.split())
for w,n in sorted((ca-cb).items()): print(f'   {n}x {w!r}')
print(f'original tokens {sum(ca.values())}, new tokens {sum(cb.values())}')"

   1x 'Rejected'
   1x 'alternatives).'
   1x 'apart.'
   1x 'can'
   1x 'copies'
   1x 'documents'
   1x 'drift'
   1x 'justifies,'
   1x 'leaving'
   1x 'natural'
   1x 'raced.'
   1x 'seam'
   1x 'separate'
   1x 'whole:'
   1x 'whose'
   1x 'would'

original tokens 6099, new tokens 6680
```

**Every one of the 16 absent tokens explained:**

- `whose would whole: natural seam separate justifies, leaving
  documents copies can drift apart.` — 13 tokens, the entire deleted
  NFR9 sentence "This document is over budget and deliberately whole:
  the natural seam would separate a decision from the requirement it
  justifies, leaving two documents whose copies can drift apart." Its
  remaining words (`This`, `document`, `is`, `two`, `a`, `the`, …) are
  still present elsewhere, which is why only 13 show. This is the
  rewrite the brief required.
- `Rejected alternatives).` — 2 tokens, from the Limitations bullet's
  "(see Rejected alternatives)", replaced by the named pointer "(see
  **Blocking `ghmem unroute` in the containment hook**,
  `docs/ghmem-rejected.md`)". The words `Rejected` and `alternatives`
  survive as the `## Rejected alternatives` heading in
  `docs/ghmem-rejected.md`, but not in this punctuated form.
- `raced.` — 1 token, "…never raced." became "…never raced
  (**Dispatch timeouts for crash recovery**, `docs/ghmem-rejected.md`)."
  The word survives without its full stop.

A sorted whitespace-normalized **block** diff was run first and agreed:
every removed block reappears as a `+` block with a pointer appended,
and the only unmatched additions are the three file headers, the
`## Commands` heading, the entry point's index paragraph, and the
§1/§2/§3 pointer paragraphs.

### Line counts — every file under 400

```
399 docs/ghmem-design.md
127 docs/ghmem-harness.md
186 docs/ghmem-decisions.md
 85 docs/ghmem-rejected.md
797 total   (712 original + 85 of headers and pointers)
```

The entry point sits at 399 with one line of headroom. That is real
but not fragile in the sense that matters: the next seam, if one is
needed, is Architecture §2 Artifact's sub-bullets — the topic-doc body
semantics (FR/NFR/Limitations/Open questions/History), ~20 lines,
consulted when writing or reviewing a topic doc rather than on every
read. It was left in place because FR4 defers to it by name and the
entry point would otherwise describe a pipeline with no stated output.

### Character width — 72 columns, measured in characters

```
$ python3 -c "<per-line len() of the rstripped decoded line>"
docs/ghmem-design.md:    max 72, over-72 lines []
docs/ghmem-harness.md:   max 71, over-72 lines []
docs/ghmem-decisions.md: max 72, over-72 lines []
docs/ghmem-rejected.md:  max 71, over-72 lines []
```

Zero violations, unchanged from the original's max-exactly-72.

Files I edited outside the four: `tools/ghmem/README.md` (over-72 at
150–151) and the three specs below (c2 at 221/265; c1 at 138/139;
d-reentry at 54/87/104/117) each carry pre-existing over-72 lines,
none of them lines I touched — my edits are all at lines 4–12.

### Inbound references

```
$ grep -rn "ghmem-design" --include="*.md" . \
    | grep -v "^./.git/" | grep -v "^./memory/"
```

| Hit | Verdict | Action |
|---|---|---|
| `docs/superpowers/specs/2026-07-24-ghmem-c2-topic-close-design.md:9` — "§Architecture (the `topic`, `close` command bullets …) and §Agents" | **Broke.** Those bullets moved. `§Agents` was already stale (the section is §3 Agentic layer). | Fixed: now names `docs/ghmem-harness.md` §Commands for the two contracts and §3 Agentic layer for the retry. |
| `docs/superpowers/specs/2026-07-23-ghmem-c1-index-design.md:9` — "§Architecture and §Artifact … the `ghmem index` bullet (parent §1)" | **Broke.** The `index` bullet moved; §Artifact stayed. | Fixed: `ghmem index` contract now cited at `docs/ghmem-harness.md` §Commands; `§2 Artifact` reference kept. |
| `docs/superpowers/specs/2026-08-11-ghmem-d-reentry-design.md:11` — "§Architecture (the `route` reducer branch …) and §Error handling" | **Broke.** The `route` bullet moved. `§Error handling` was already stale (the section is §4 Failure handling). | Fixed: names `docs/ghmem-harness.md` §Commands and §4 Failure handling. |
| `docs/superpowers/specs/2026-07-24-ghmem-agents-design.md:4, :346` — "§Agentic layer, §Validation, §Execution structure" | **Did not break.** §Agentic layer is Architecture §3 and stayed. `§Validation` and `§Execution structure` name sections that do not exist — pre-existing drift, unrelated to this split. | Left alone; reported here. |
| `docs/ghmem-changelog.md:4, :104–115` | Names the doc, no section. Still true. | None. |
| `tools/ghmem/README.md:8` | Names the doc, no section. Still true but incomplete — a reader following it would not learn the siblings exist. | Added three lines naming the three detail files. |
| `docs/superpowers/plans/2026-07-11-*`, `2026-07-15-*`, `2026-07-17-*` (5 files) | "Copied from the spec (`docs/ghmem-design.md`)" — no section named. Still true. | None. |
| `plans/unroute-event/requirements.md:321`, `plans/unroute-event/classification.md:66–85`, `plans/*/reports/*.md`, `.claude/handoff-*.md` | Point-in-time reports and handoffs describing the doc as it was when they were written. Not living cross-references. | None. Note `.claude/handoff-todo.md:4` carries a recommendation against splitting, now superseded by this work. |
| `memory/ghmem-project.md:12` — "The design and its rationale live in `docs/ghmem-design.md`" | **Now imprecise.** The design lives there; the rationale lives in `docs/ghmem-decisions.md`. | **Not edited** (submodule). See "Needs a decision". |

### Rejected alternatives — all 13 present

```
$ grep -c '^\*\*' docs/ghmem-rejected.md
13
```

An existing tool · Shelling out to `gh api graphql` · A companion
procedure skill for the agents · A deny-pattern hook · Blocking `ghmem
unroute` in the containment hook · A `--session` selector on `unroute`
· Refusing to unroute a `no-signal` verdict · Relaxing
`record_verdict` to accept a live re-route · Dispatch timeouts for
crash recovery · Digest-only tiers in the synthesis context · Delta
machinery for incremental synthesis · GH Archive for historical
`author_association` · Heat-ordered `INDEX.md`

All 20 design decisions are likewise intact (`grep -c '^\*\*'
docs/ghmem-decisions.md` → 20).

### `git status --porcelain`

```
M  .claude/handoff-task.md
M  .claude/handoff-todo.md
 M docs/ghmem-changelog.md
 M docs/ghmem-design.md
 M docs/superpowers/specs/2026-07-23-ghmem-c1-index-design.md
 M docs/superpowers/specs/2026-07-24-ghmem-c2-topic-close-design.md
 M docs/superpowers/specs/2026-08-11-ghmem-d-reentry-design.md
 M plans/ghmem-design-migration/reduction-report.md
 M plans/ghmem-design-migration/report.md
 M plans/unroute-event/requirements.md
 M tools/ghmem/README.md
 M tools/ghmem/src/ghmem/cli.py
 M tools/ghmem/src/ghmem/exceptions.py
 M tools/ghmem/src/ghmem/ledger.py
 M tools/ghmem/src/ghmem/models.py
 M tools/ghmem/src/ghmem/routing_reduce.py
 M tools/ghmem/src/ghmem/store.py
 M tools/ghmem/src/ghmem/sync_core.py
 M tools/ghmem/src/ghmem/triage.py
 M tools/ghmem/tests/test_cli_unroute.py
 M tools/ghmem/tests/test_unroute.py
?? docs/ghmem-decisions.md
?? docs/ghmem-harness.md
?? docs/ghmem-rejected.md
?? plans/ghmem-design-migration/reports/
?? plans/unroute-event/reports/
```

**Files this task touched — eight, all listed:**
`docs/ghmem-design.md`; the three new `docs/ghmem-{harness,decisions,rejected}.md`;
the three specs `2026-07-23-ghmem-c1-index-design.md`,
`2026-07-24-ghmem-c2-topic-close-design.md`,
`2026-08-11-ghmem-d-reentry-design.md`; and `tools/ghmem/README.md`.
This report is the ninth, under the already-untracked
`plans/ghmem-design-migration/reports/`.

Everything else in that listing was already modified before this task
began — the `tools/ghmem/src` and `tests` changes, the plan reports,
`docs/ghmem-changelog.md`, `plans/unroute-event/`, and the staged
`.claude/handoff-*.md`. `git diff tools/ghmem/README.md` shows 92
added / 15 removed, of which mine are the three lines naming the
detail files; the rest was already there.

Nothing committed. No branch created or switched.

## Needs a decision

1. **`memory/ghmem-project.md:12`** reads "The design and its
   rationale live in `docs/ghmem-design.md`". The rationale now lives
   in `docs/ghmem-decisions.md`. Suggested replacement, for whoever
   owns the submodule: "The design lives in `docs/ghmem-design.md`,
   which names the detail it carries — command contracts in
   `docs/ghmem-harness.md`, rationale in `docs/ghmem-decisions.md`,
   refused designs in `docs/ghmem-rejected.md`." Not edited: the
   memory store is a submodule with its own gate, and the brief
   excluded it.

2. **`.claude/handoff-todo.md:4`** carries a standing recommendation
   *against* this split ("keep it whole — those two are core sections
   precisely so rationale sits beside what it justifies"). That
   recommendation is now overtaken. It is a staged file (`M ` in the
   index) and outside this task's scope, so it was left as it stands;
   whoever commits should decide whether to retire the item.

3. **Assumption made, not settled from the material:** Design
   decisions and Rejected alternatives went to *separate* files rather
   than one 257-line "rationale" file. The brief names them as two
   distinct triggers — "about to change this" versus "about to propose
   that" — and a reader with the second question should not have to
   page past 178 lines answering the first. Merging them later is one
   `cat` and a pointer rewrite if that proves wrong.

4. **Not done, deliberately:** no changelog entry was added to
   `docs/ghmem-changelog.md`. The brief did not ask for one and that
   file is already modified by other work; adding a dated entry is the
   committing session's call.
