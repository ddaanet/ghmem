# ghmem rejected alternatives

Designs considered and refused, with the reason each was refused. Read
it before proposing a change to `docs/ghmem-design.md`: these are
settled questions, and an entry here is what stops one being reopened.
The argument for what was kept instead is in
`docs/ghmem-decisions.md`.

## Rejected alternatives

**An existing tool.** Nothing on the market fills the need. The
commercial tools — DeepWiki, Swimm, dosu, kapa — document *current
code*, not decision history. Unblocked is closest in spirit but is a
closed hosted index with no exportable docs. Among research
prototypes, DRMiner (ASE 2024, Jira-only, ~65% F1) proves that LLM
design-rationale mining works, and CommitDistill (GitHub-native,
deterministic extraction) is the closest architectural match but
outputs a JSON store rather than prose and does not treat rejected PRs
as first-class. No tool combines GitHub-native, rejected-PR-aware and
incremental-into-versioned-markdown.

**Shelling out to `gh api graphql`.** `--paginate` handles only a
single top-level connection, so nested comment and review pagination
would be hand-rolled either way. githubkit gives typing and retry for
the same work.

**A companion procedure skill for the agents.** A registered skill
injects its description into every session's context and is skipped
silently if it is preloaded and then missing. The procedure text lives
inline in each agent definition instead.

**A deny-pattern hook.** Deny lists both over-block and are trivially
slipped. The allowlist admits a known-good shape and refuses
everything else.

**Blocking `ghmem unroute` in the containment hook.** The escalation
that would motivate it does not exist: `unroute` clears the dispatch
along with the route, so a worker that unroutes an item it was
rejected on immediately fails `record_verdict`'s not-dispatched guard,
and its only way back is to be served the item again through the
metered queue. The residual — a confused worker returning other
sessions' items to the queue — is not a new class of exposure, since
the hook is a command-*shape* allowlist that already permits `ghmem
requeue` and `ghmem rebuild`, both corpus-wide mutating operator
commands. Deciding which subcommands a caller may run needs authority
vocabulary the hook does not have: it sees one command string, and one
copy of it contains both agents.

**A `--session` selector on `unroute`.** Session identity is a
property of a particular correction job, not of the command; the
numbers are derivable once with a query over the routing mirror, and
the command stays in ghmem's one vocabulary of issue/PR numbers.
Adding the selector later is additive, removing it would not be.

**Refusing to unroute a `no-signal` verdict.** That would make it the
one verdict that can never be corrected, and it is the population most
likely to need correcting.

**Relaxing `record_verdict` to accept a live re-route.** The freshness
check is what distinguishes a live re-route from a reentered item's
re-triage. Clearing the route reaches the same goal without touching
that distinction.

**Leaving a re-routed topic's emptied row dirty.** A re-routed item is
still in the corpus under another name, so the topic it left has a
rewrite to receive, and the dirty row it keeps is what carries that
rewrite to a synthesis worker — the argument for treating a re-route
unlike a withdrawal. Overruled: `dirty` means "needs re-synthesis" and
is what `status` dispatches synthesis workers from, so an empty dirty
row is not a marker but a worker sent at a topic no route names, which
`topic.assemble` refuses with `TopicNotRoutedError` — precisely the
breakage the emptied-topic rule exists to remove. Marking the orphaned
doc instead needs a third topic state distinguishing "synthesize me"
from "delete me", a larger design than this, and the orphan sweep it
would serve does not exist.

**Dispatch timeouts for crash recovery.** A timeout races a
slow-but-live session. Explicit reclaim between orchestration runs
cannot.

**Digest-only tiers in the synthesis context.** The standing rules
need who-said-what verbatim — a two-sentence note cannot back an FR
citation — and triage has already filtered: the routed set is about a
third of the corpus, so full source across all topics is ~1–1.5M input
tokens, inside the backfill envelope.

**Delta machinery for incremental synthesis.** The measured saving is
immaterial against micro's traffic, and full reload yields better
rewrites.

**GH Archive for historical `author_association`.** Historical values
are recoverable only for ~2018 to October 2025 — the field appeared in
event payloads around 2018 and GitHub removed it in October 2025 — and
access means a BigQuery dependency with unverified scan costs.
Computed standing covers the whole decade uniformly.

**Installing the agent definitions into the consumer's
`.claude/agents/`.** Frontmatter `hooks:` do attach there, which would
keep containment on the definition. It needs an install step in every
consumer project and an exact-path workspace-trust key for each one in
`~/.claude.json` — inherited trust from a parent directory does not
count, and the hooks are dropped silently without it. Hooks registered
by the plugin need neither.

**Heat-ordered `INDEX.md`.** Heat ordering would churn the whole file
on every sync; alphabetical ordering keeps diffs stable and reviewable.
