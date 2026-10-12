# ghmem harness — command surface and storage

The contract of each `ghmem` command and the shape of the two ledgers
behind them. Read it when changing what a command emits, accepts or
records, or when working on the corpus store. It is Architecture §1 of
the design doc, `docs/ghmem-design.md`; the argument behind these
shapes is in `docs/ghmem-decisions.md`.

## Commands

- `ghmem sync` — GraphQL-batched fetch (githubkit; explicit nested
  cursors) of all issues, PRs (including review threads and review
  comments, `mergedBy`/`mergedAt`, and fetch-time `author_association`
  as the hint column) and discussions, plus the default branch's
  commit log, into the fetch ledger. PR diff text comes from the REST
  diff endpoint. Cursor on `updated_at`; resumable mid-run;
  rate-limit aware, tracking GraphQL point cost and sleeping or
  exiting cleanly on exhaustion.
- `ghmem rebuild` — replays both ledgers into SQLite, then appends
  FR7's `reenter` events, each freeing a dispatch so its item rejoins
  the triage queue. Because it writes to the ledger, it runs between
  orchestration runs, never mid-session.
- `ghmem show <number>` — one thread rendered as compact markdown:
  body, comments, review threads, the PR diff (truncated when
  outsized, full file list always shown), cross-references, reactions,
  linked commits. The agent-facing read primitive. FR8's standing
  annotation is rendered inline here and in `triage` and `topic`
  output — e.g. `dmaluka (maintainer since ~2022; 180 merges at time
  of comment)`.
- `ghmem discussion <number>` — the read primitive for GitHub
  Discussions, which carry their own number sequence disjoint from
  issues and PRs (hence a distinct command, not a `show` mode: number
  `5` can name both an issue and a discussion). Renders body, then
  comments interleaved with their one-level replies, the accepted
  answer marked, and reactions — no diff, reviews, merge line or
  cross-references. Discussion activity is display-only and does not
  feed the standing derivation.
- `ghmem heat` — deterministic ranking over the corpus as CSV, hottest
  first: comment count, reaction count, distinct participants,
  cross-reference degree, and recurrence span (years between first and
  last activity). CSV because its only external consumer is analysis,
  which reads the whole distribution column by column; the in-process
  consumer is `triage`, which takes the hottest untriaged item.
- `ghmem triage` — opens a metered batch session: mints a session
  token and yields the next untriaged item in heat order as a complete
  packet — a `session:` line, a `packet-chars:` header, the id, a
  `topics:` line naming every topic minted so far, and the item's
  `show` rendering. The topic line sits inside the budgeted body, so
  an oversized first item's diff allowance is computed over a floor
  that already carries it. The CLI meters items and characters served
  per token through ledger events, and the budget gate runs **before**
  serving: if the next item's packet would exceed the remaining
  budget, the batch ends with that item still queued — empty stdout,
  `batch complete: budget exhausted` on stderr, exit 0. Triage-complete
  has the same shape (`triage complete: 0 pending`): exit codes are
  reserved for actual errors, so an expected outcome never reads as a
  failure. Yielding records a `dispatched` ledger event, so concurrent
  sessions hand out distinct items.
- `ghmem route <token> <number> <verdict>` — records the verdict in
  the ledger, marks the topic dirty, resolves the dispatch, and
  immediately yields the next packet under the same session (or the
  terminal message): one command per item, so the agent sits in no
  deterministic step. The note arrives on stdin, and only on stdin, as
  a **single line**: `route` strips it and refuses one that is empty
  or carries a newline.
- `ghmem unroute <number>...` — withdraws each named item's verdict,
  returning it to the triage queue. It deletes the item's route and
  its outstanding dispatch together, because three gates each exclude
  an item carrying a fresh route and only the row's absence clears all
  three at once without relaxing any of them. Every number is resolved
  against the corpus before any event is appended, so a typo late in a
  batch leaves no half-applied correction; a number carrying no verdict
  is not an error, which is what makes re-running a batch a no-op. An
  operator command, not a session one: it takes no token, and a freed
  item rejoins the queue rather than becoming routable on the spot.
  Refused: **A `--session` selector on `unroute`**
  (`docs/ghmem-rejected.md`).
- `ghmem requeue` — returns every dispatched-but-unrouted item to the
  queue at once. Run at orchestration-run start, before any agent is
  spawned: serving a stranded item mid-session would hand out a packet
  with no session to route it. It requeues, prints the count, and
  serves nothing.
- `ghmem status` — coverage summary: one counts line carrying `total`,
  `triaged`, `routed`, `no_signal`, `pending` and `dispatched`, where
  `dispatched` is the stranded set — dispatched but not yet routed —
  that `requeue` reclaims. Then the full list of known topics (so
  triage can consult it before minting a near-duplicate), the dirty
  subset, and each connection's sync cursor position.
- `ghmem topic <name>` — assembles the synthesis context for one
  topic: an item index — one entry per routed item, a deterministic
  header rendered from the corpus (title, state, dates, author with
  standing, comment, reaction, participant and related-item counts;
  for PRs the diffstat) plus the **routing note** recorded at triage —
  followed by the **full thread text, diff included, of every routed
  item**. The context is headed by a **snapshot token** minted for this
  assembly and recorded in the ledger with a content digest over the
  topic's routed set.
- `ghmem close <token>` — records synthesis completion, mirroring how
  `route` answers `triage`. Compare-and-set against the token's
  digest: the dirty flag clears only if the topic's routed set is
  unchanged, so routes that arrived between assembly and close-out
  leave the topic dirty for the next run rather than being silently
  discarded. Successful close-out regenerates `INDEX.md`.
- `ghmem index` — regenerates `docs/design/INDEX.md` from the topic
  docs' frontmatter `description:` lines, ordered alphabetically by
  topic name. Alphabetical gives stable diffs; heat ordering would
  churn the whole file on every sync. Each line ends with the
  issue/PR numbers its doc cites, heat-ordered and capped at five —
  the full set stays in the doc, since an index line is a hook and not
  a bibliography.

**Storage.** Two append-only JSONL ledgers, mirrored into a derived
SQLite cache for querying. The mirror is defined and accessed through
SQLAlchemy 2.0 typed declarative models (`Mapped`/`mapped_column`,
`DeclarativeBase`), so schema, upserts and row-loading come from the
declarative layer rather than hand-rolled DDL and cursor code. The
**fetch ledger** (`corpus/fetch.jsonl`) records every raw item as
`sync` fetches it — `raw_node`, `cursor` and `fetch_failed` events —
so rebuilding SQLite is a pure local load with no API round-trip.
Regenerating it from GitHub instead would be hours of rate-limited
fetching, and the ledger also feeds test fixtures; it is gitignored
for size. The **routing ledger**
(`corpus/routing.jsonl`) — `dispatched`, `route`, `requeue`,
`reenter`, `unroute`, `snapshot` and `close` events — is committed: it
is the system's own memory and what makes runs reproducible. SQLite
(`corpus/cache.sqlite`) is regenerable from the two ledgers and is
gitignored.
