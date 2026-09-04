# ghmem subproject 2 Part C1 — `ghmem index`

Design spec. Regenerates `docs/design/INDEX.md` — the recall surface —
from the topic docs beside it plus the routing mirror. First of two
slices carved out of Part C; C2 (`topic` + `close`, the snapshot-token
machinery) is specced separately and depends on the `build_index` this
slice lands.

Parent spec: `docs/ghmem-design.md`, §Architecture and its §2
Artifact. This slice implements the `ghmem index` contract
(`docs/ghmem-harness.md` §Commands) and the `INDEX.md` bullet
(parent §2). It settles four questions the parent
left open, each grounded in a scan of the live corpus rather than
assumption.

## Why this is its own slice

Part C as specced is three commands (`topic`, `close`, `index`), a
routing-ledger extension (`SnapshotEvent`, `CloseEvent`), and a new
`Snapshot` table. A task whose contract runs past ~400 lines predicts
an implementation that overshoots the same cap, so it splits at spec
time. `index` is the clean seam: it is the one piece with no ledger
semantics and no snapshot story, it reads state the dispatch half
already writes, and `close` regenerates `INDEX.md` — so `index` wants
to exist first, giving `close` a real function to call instead of a
stub.

## What `index` does

`ghmem index` takes no arguments. It reads every `<topic>.md` beside
`INDEX.md`, reads the `Topic` list from the routing mirror, and writes
one regenerated `INDEX.md`. It creates `docs/design/` if absent — the
first live run's lead finding was a command dying on a directory
nothing had created, and this applies the lesson before it can recur;
subproject 3 still owns scaffolding the directory's *contents*.

It prints one recap line naming what it did, per the project's
quiet-command rule (a command that did work signs off with the work,
never a bare "OK"):

```
INDEX.md: 12 topics (9 synthesized, 3 pending)
```

### Path resolution

`paths.py` grows `design_dir()` beside `corpus_dir()`, the same shape:
honour `$GHMEM_DESIGN_DIR` when set, otherwise `<git-toplevel>/docs/
design`. The environment override is what lets the test suite point the
command at a tmpdir, exactly as `$GHMEM_CORPUS_DIR` already does for the
corpus.

## Citation extraction — the settled model

An index line ends with the issue/PR/discussion numbers the topic
**rests on**. The parent spec says "from the topic docs' frontmatter"
in one place and "the issue/PR numbers it rests on" in another; this
slice resolves the tension against what the corpus actually contains.

### The numbers come from the doc body, not frontmatter

The numbers a doc rests on are the ones its prose cites. Reading them
from the body means nothing is maintained in two places and nothing can
drift; it also composes with the parent's structural guard (an FR with
no citation does not get written), so a doc's citation set *is* its
evidence base. Frontmatter carries `description:` — prose only, the
hook line — and nothing else this command reads.

### Bare `#N` is unambiguous in this repo — corpus scan

The concern was that `#123` could mean an issue, a PR, or a discussion,
and that discussions have no heat score. The live corpus refutes the
ambiguity outright:

- Issues/PRs occupy numbers 1..4148 (3875 present).
- Discussions occupy 2450..4149 (245 present).
- **Their intersection is empty.** GitHub allocated all three kinds
  from one per-repo counter, so a number belongs to exactly one kind.
  Set membership disambiguates precisely; no naming convention need be
  imposed on quoted prose, and no agent tier is needed to arbitrate —
  there is nothing to arbitrate.

A scan of all corpus prose found 1938 bare `#N` outside code spans:
55.3% resolve to an issue, 43.0% to a PR, 1.0% to a discussion, and
0.7% (14 occurrences) to nothing in the corpus. Every one of those 14
was read in context: all are non-citations — hex colours (`#282828`,
`#303030`), kernel build strings (`#81-Ubuntu SMP`), other projects'
trackers (`golang/go#42684`). **The corpus-membership check that
disambiguates is the same check that rejects every false positive.**
The regex does not need to be clever; it needs to be checked against
the corpus.

### Only the synthesizer's own prose counts — block quotes excluded

No topic doc exists yet, so the scan cannot say which references a
synthesized doc rests on — only how humans write them. The design
quotes maintainers verbatim, so corpus prose (and its incidental `#N`
mentions) will land inside the docs as block quotes. A citation is a
reference the synthesizer asserts in **its own** prose (FR/NFR/
Limitations/History lines); quoted material is evidence *for* those
assertions, not an independent claim. Extraction therefore drops
`>`-prefixed lines before matching. This also kills a failure mode of
heat ordering: without it, a quoted comment mentioning a mega-thread
like #93 in passing would outrank the doc's actual subject, and the
most-mentioned corpus threads would colonize every index line
regardless of topic.

### Extraction algorithm

Given a topic doc body, in order:

1. Strip fenced code blocks and inline code spans
   (`` ```…``` `` and `` `…` ``).
2. Drop `>`-prefixed (block-quote) lines.
3. Match `(?<![\w/])#(\d+)\b` in what remains, in appearance order.
4. Keep only numbers the corpus resolves (present in `issue_or_pr` or
   `discussion`).

The lookahead guard and the corpus check do different jobs: the guard
keeps `#N` out of URLs and identifiers, the corpus check rejects
non-citations. The scan shows the corpus check is what earns its keep.

**Bare `#N` only** — no `github.com/...` URLs, no `owner/repo#N` short
forms. Those are how *humans* wrote references (786 of 1107 corpus URLs
point at this repo under its former `zyedidia/micro` name), and human
prose is excluded by the block-quote rule anyway. The synthesizer's own
reference form is ours to fix in subproject 3's prompt, so one form
suffices. A deliberate YAGNI call, cheap to revisit: the extractor is
one pure function over text.

## INDEX.md format

```markdown
# Design topic index

<!-- Generated by `ghmem index` from the topic docs. Do not edit. -->

- [clipboard](clipboard.md) — must work over ssh without X forwarding; OSC 52 replaced shelling out to xclip (#93, #1138, #2374, #1746, #3107, …)
- [options-parsimony](options-parsimony.md) — a new option needs an argument against every alternative that reuses an existing one (#2374, #3107)
- softwrap — *not yet synthesized* (#93, #3127, #1138, #249, #174, …)
```

- **Alphabetical by topic name.** Stable diffs; heat-ordering the list
  itself would churn the whole file on every sync. (Heat orders the
  *numbers within a line*, not the lines.)
- **Synthesized and pending interleaved in one list.** The reader is
  looking up a subject, not auditing synthesis progress. A synthesized
  line links to its file and carries its `description:` verbatim. A
  pending line — a topic triage routed items to but no one has
  synthesized — has no link (no file exists) and takes its numbers from
  the routed items instead of a body it does not have. Indexing pending
  topics is the deliberate choice over the parent's literal "from the
  topic docs" reading: this project's premise is that unwritten
  knowledge is the failure mode, so the recall surface must not be
  silent about knowledge the system knows it holds. The gap is then
  visible to the human deciding whether to run another synthesis pass.
- **Number suffix caps at five, ordered by heat, `…` when more.** An
  arbitrary but real cap — a hot topic can cite thirty numbers and a
  thirty-number line is unreadable. Heat ordering surfaces the numbers
  that matter without a second judgement call. The full citation set
  stays in the topic doc's own body, which is its home; the index line
  is a hook, not a bibliography.
- **Discussions sort after scored items** (among themselves by number
  ascending), because `heat_scores` runs over `IssueOrPR` only and
  giving discussions a heat score is subproject 4's business, not a
  docs command's.
- **The generated-file comment is load-bearing.** The parent spec says
  INDEX.md is never agent-edited; the file must say so where an agent
  will see it.

## Module structure

New module `index_cmd.py` (the name avoids shadowing the builtin
`index` and the `index=True` column kwarg), three functions matching
three concerns:

- `cited_numbers(body: str) -> list[int]` — the pure text extractor
  above. No store, no I/O: a doc body in, resolvable-looking numbers in
  appearance order out. (Corpus resolution is applied by the caller,
  which holds the store; this returns syntactic matches so the two
  rules are testable apart. See Open questions.)
- `build_index(store: Store, design_dir: Path) -> IndexSummary` — reads
  `design_dir/*.md`, splits frontmatter from body, extracts and
  corpus-filters citations, heat-orders survivors, folds in the store's
  topic list for pending topics, returns a structured summary with the
  rendered lines. The store dependency lives here and only here.
- `render_index(summary: IndexSummary) -> str` — summary to markdown.
  Pure, so the format is a plain string test.

`store.py` grows one method, `heat_scores_for(numbers)`, wrapping a
filtered heat query so `index_cmd` reaches heat through the same store
surface every other command uses and never imports `heat_query`
directly. The query is `_ranked_query(weights).where(item.number
.in_(numbers))` then `_run` — the shape `untriaged_heat_scores`
already has, a third entry point beside it in `heat_query.py`.
Full-corpus heat is 12.7s after Part C's indexing work; a filtered
query is the difference between a command run after every `close` and
one avoided.

`cli.py` gains a fourth `@cli.command`: open the store, call
`build_index`, write the file, print the recap.

Frontmatter parsing uses `msgspec.yaml` (already a dependency via
`msgspec`) — no new package. `description:` is the only field read.

## Edge cases

- **No `docs/design/` and no topics** — create the dir, write an
  INDEX.md with the header and no entries, recap `0 topics`. Empty is a
  valid state, not an error.
- **A doc with no resolvable citations** — indexed with its
  description, no number suffix. Legitimate for an all-NFR norm topic.
- **A doc filename matching no known topic** — indexed as-is from its
  frontmatter. `index` trusts the docs; reconciling doc-vs-routing
  membership is `status`'s job.
- **A routed item never fetched into the corpus** (the first-page
  truncation finding, or an item deleted upstream — 29 numbers in
  1..4148 are absent) — the number simply fails corpus resolution and
  does not appear. No special path.
- **Malformed frontmatter or missing `description:`** — raise, do not
  paper over. A synthesized doc without its hook line is a synthesis
  bug the human should see, not a blank line silently indexed. This is
  the one place the command is strict rather than lenient.

## Testing

Three seams, one per function:

- `cited_numbers` — unit tests over crafted bodies: a fenced block, an
  inline span, a `>`-quoted `#93`, a hex colour, a URL-embedded number,
  a genuine citation. Each asserts in/out. (These test the syntactic
  rules; corpus resolution is tested at `build_index`, which holds the
  store.)
- `render_index` — string tests over hand-built summaries: a
  synthesized line, a pending line, the five-number cap with `…`, an
  empty index.
- `build_index` — integration tests over a tmp `design_dir` plus a
  small fixture store, asserting the end-to-end line set including heat
  order and the synthesized/pending split.

Mutation targets the plan must name, each with the test that must die
(the project's guard against green-for-the-wrong-reason tests):

- Invert the block-quote strip → a quoted citation wrongly appears.
- Drop the corpus filter → a hex colour leaks into a line.
- Break the five-cap → a sixth number shows.
- Remove the heat ordering → numbers appear in body order, not heat
  order.

## What C1 explicitly does not touch

No ledger events, no new tables, no snapshot token, no `close`. `index`
reads the existing `Topic` mirror and the filesystem and writes one
file. All snapshot / compare-and-set machinery is C2. This keeps C1
well under the 400-line artifact cap and hands C2 a real `build_index`
to call.

## Scope moved out

The first live run recorded 44 `fetch_failed` records that no command
surfaces, and an earlier framing filed that under "what `index` does
about them". It does not belong here: `index` is about topic docs, and
incomplete *fetches* are a corpus-coverage fact — `status`'s territory.
Treat it as a small separate `status` change, not part of C1.

## Open questions

- **`cited_numbers` return type and the resolution seam.** The spec has
  it return syntactic matches and the caller apply corpus resolution,
  so the two rules test apart. If that makes `build_index` awkward
  (e.g. it wants order preserved across the filter), the resolver could
  move into a two-arg `cited_numbers(body, resolver)` instead. Decide
  at plan time from the call site.
- **`msgspec.yaml` on a one-line `description:`.** If `msgspec.yaml`
  pulls a transitive YAML dependency or is heavier than warranted for a
  single scalar field, a five-line hand-parser for `description:` is
  the fallback. Confirm the dependency is genuinely free at plan time.

## History

Carved from Part C during the 2026-07-23 design pass, after the first
live-data run gave Part C real evidence to design against. The four
resolved questions (body-vs-frontmatter citations, `#N` ambiguity,
block-quote exclusion, pending-topic indexing) were each settled
against a scan of the live corpus, not assumption — the number-space
overlap in particular was expected and turned out empty, which
collapsed a planned hybrid regex/agent disambiguation to a single set
membership test.
