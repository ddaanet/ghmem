# ghmem subproject 3 — the agentic layer

Design spec for the third of ghmem's four subprojects. The parent
spec `docs/superpowers/specs/2026-07-09-ghmem-design.md` is the source
of truth for the system as a whole and for the standing rules this
subproject enforces; this document specifies only the agentic layer
and does not re-derive decisions settled there.

## Scope

Subprojects 1 (corpus) and 2 (dispatch) built the deterministic
harness: fetch, heat ranking, the metered `triage`/`route` session
loop, `topic`/`close` with snapshot compare-and-set, and `index`. That
harness has produced zero memory — `routing.jsonl` does not exist and
`docs/design/` holds no topic doc. This subproject builds the layer
that finally runs items through the harness and writes the artifact.

The whole subproject is three things plus a one-line environment
change:

- `.claude/agents/ghmem-triage.md` — the triage agent (Sonnet).
- `.claude/agents/ghmem-synthesize.md` — the synthesis agent (Opus).
- `tools/ghmem/hooks/scripts/ghmem-only.sh` — the Bash allowlist
  hook, shared by both agents; plus
  `tools/ghmem/hooks/scripts/scope-docs-design.sh`, the write-path
  hook the synthesis agent alone carries.
- A `PATH_add` line in the repo-root `.envrc`.

Out of scope, deliberately:

- **Orchestration.** The incremental loop (`sync` → `triage
  --requeue` → triage the delta → synthesize dirty topics) stays a
  manual sequence of spawns run by hand. The parent spec defers the
  loop driver ("a skill-driven session now and an Agent-SDK script
  later… the natural endgame, not a prerequisite"), and there is a
  sharper reason to wait: a driver written before the agents have
  been watched running by hand bakes in guesses about where they
  stall. Build the driver after this subproject, informed by real
  runs.
- **Routing accuracy.** Whether triage routes *correctly* is
  subproject 4's ground-truth gate (the wrapindent-cluster comparison
  with distractors). This subproject's bar is mechanical correctness
  only — see Done criteria.
- **The `docs/design/` scaffold.** It already exists as a mechanism,
  not an artifact to author — see Bootstrap.

## Invocation

Add to the repo-root `.envrc`:

```
PATH_add tools/ghmem/.venv/bin
```

`tools/ghmem/.venv/bin/ghmem` is the entry point built by `uv sync`
(`[project.scripts] ghmem = "ghmem.cli:main"`). The nested
`tools/ghmem/.envrc` already puts it on PATH inside that directory,
but agent Bash calls run from the repo root, where only the root
`.envrc` is in effect — so bare `ghmem` does not resolve there without
this line. Corpus and design-dir locations are cwd-independent
(`paths.corpus_dir()` / `paths.design_dir()` anchor to the git
toplevel), so an agent runs `ghmem` from the repo root and its writes
land under `docs/design/` regardless. This matches the venv-activation
pattern in the edify and handoff siblings' `.envrc` files.

A `.envrc` edit takes effect only after Claude Code restarts (same as
a new `.claude/agents/*.md` file) — its Bash tool's environment is
fixed for the life of a session, so `PATH_add` will not resolve `ghmem`
for an agent spawned inside the same session the line was added in.
Confirmed live: restarting picked up both this line and the new agent
definitions in one step.

**Dispatch these agents without a custom name.** Spawn them by
`subagent_type` alone. Passing a `name` overwrites the dispatch's
`agentType` with that name and demotes the real definition to a
separate `customAgentType` field; frontmatter hooks are registered by
resolving `agentType`, so a named dispatch resolves nothing, registers
zero hooks, and runs with an unrestricted shell. Nothing reports this
— there is no error on either side, and the agent cannot tell.

The cost is only that a worker cannot be addressed by name mid-flight,
which triage and synthesis never need. The benefit is the containment
the whole design rests on. This is not a stylistic preference: every
ghmem worker dispatched before 2026-07-25 carried a name, and every
one of them ran uncontained for that reason alone.

## Agent: ghmem-triage (Sonnet)

**Frontmatter.** `tools: Bash`; one PreToolUse hook (see The allowlist
hook). No other tool: its only data path is `ghmem` over the local
corpus.

**Input.** None — no item content, no budget numbers. The CLI owns the
batch budget; the agent never counts characters or items.

**Loop.** Open a session with `ghmem triage`, which mints the session
token and serves the first packet. Each packet leads with a `session:
<token>` header line (and a `packet-chars:` line); the agent reads the
token from that header and passes it to every verdict. Then repeat:
judge the packet, answer with exactly one

```
ghmem route <token> <number> <verdict> <<'EOF'
<the note, on one line>
EOF
```

and receive the next packet — `route` serves it, so `triage` is called
once per session — until the CLI prints a terminal status line. One
`route` per item, so no step is spent idling.

**Judgment (agent-body prose, written at build time).** A verdict is a
topic name or the reserved `no-signal`. `no-signal` is the comfortable
default, not a failure — most items carry no knowledge worth a topic
doc. The trial is what the text carries, never whether the thread
concluded: substantial maintainer feedback on an open PR is knowledge,
and the corpus is kept in sync rather than photographed, so a later run
captures what the thread becomes. Before proposing a *new* topic,
consult the current topic list to avoid near-duplicates. The CLI-facing
id is the item number (matching `ghmem show <number>`).

**Final report.** Lead with the terminal status line, verbatim, so
the orchestrator branches on line one:

- `batch complete: …` — this worker stops; the corpus has more; spawn
  another batch.
- `triage complete: …` — the corpus is exhausted; stop spawning.

Then the id → verdict lines. The CLI keys on `ServeResult`'s
packet-xor-message structure, never on message text, so the exact
terminal strings are the CLI's to word.

## Agent: ghmem-synthesize (Opus)

**Frontmatter.** `tools: Bash, Read, Write, Edit`; two PreToolUse
hooks — the shared Bash allowlist, and a write-path hook scoping
`Write`/`Edit` to `docs/design/`.

**Input.** One topic name. In manual operation a human (later, the
loop driver) reads the dirty topics from `ghmem status` and spawns one
synthesis agent per topic. One topic per spawn — no batching: a hot
topic's context plus a rewritten document already dwarfs the fixed
spawn overhead that motivates triage batching, and prose quality (the
point of the Opus tier) is what accumulated-context rot degrades
first.

**Flow.**

1. `ghmem topic <name>` returns the assembled context and a snapshot
   token.
2. Create or update `docs/design/<name>.md`: frontmatter with a
   `description:` hook line, then the body.
3. `ghmem close <token>`. On a clean close the harness appends the
   `CloseEvent`, clears the topic's dirty flag, and regenerates
   `INDEX.md`.

**Stale token.** Activity landing mid-synthesis makes the token stale;
`close` writes nothing and reports `stale: …` on stderr, exit 0. This
is a normal race, not an error to escalate: re-assemble from the fresh
snapshot, rewrite the doc, and close again. Stop after two consecutive
stale tokens and report.

**Standing rules with teeth (agent-body prose; full form in the parent
spec).** FR/NFR/Limitation entries require maintainer-grade backing —
a statement by someone holding standing at that time, or actual
merge/reject behavior. User chorus is demand evidence, never policy.
History and Open-questions attribute positions to their holders by
standing, since a live debate's front line is where the people with
merge power currently stand. The agent reads standing off the
`ghmem`-rendered context; it never derives it.

## The allowlist hook

Both agents process or emit against a corpus built from untrusted
GitHub text (issue and comment bodies that can carry prompt
injection). The hook is the hard boundary that keeps a subverted agent
on its only sanctioned data path.

**Mechanism.** A PreToolUse hook declared in each agent's own
frontmatter under `hooks:`. A frontmatter hook is scoped to that agent
and torn down when it finishes, so no global `settings.json` state and
no per-call agent-identity branching are needed. A blocking hook
(exit 2, stderr becomes the reason) takes precedence over allow-rules
and over `bypassPermissions`/`auto` mode — the allowlist cannot be
escaped by the parent session's permission mode.

**Layout.** Both scripts live under `tools/ghmem/hooks/scripts/`, and
their bats suites under `tools/ghmem/tests/` beside the pytest ones.
That is the shape a Claude Code plugin takes — `hooks/scripts/` under
a plugin root — so packaging ghmem as a plugin later moves the
directory without rearranging it, and the frontmatter path changes
only its prefix (`${CLAUDE_PROJECT_DIR}/tools/ghmem` →
`${CLAUDE_PLUGIN_ROOT}`). Keeping the tests in the Python package's
`tests/` is what puts them inside `just green`'s cache inputs, so a
hook edit can no longer go unverified.

**Bash allowlist — `tools/ghmem/hooks/scripts/ghmem-only.sh`, shared
by both agents.** Matcher `Bash`. The script reads `.tool_input.command` from
stdin JSON and allows the call *iff* the command is a single `ghmem`
invocation, via a positive character allowlist:

```
^ghmem([ \t][A-Za-z0-9_./:=-]+)*$
```

The separator class is `[ \t]`, not `[[:space:]]`: POSIX `[[:space:]]`
includes the newline character, which would let a `ghmem status\nrm
-rf /`-style command slip through as a single match (a bats test
caught this live).

A match exits 0; anything else prints a reason to stderr and exits 2.
The allowlist is positive by construction — none of `; & | $ ` `` ` ``
`< > ( )` or newline is in the permitted class — so command chaining,
substitution, pipes, and redirects are rejected without enumerating
metacharacters (a denylist is far harder to get exhaustively right).
The matcher gates only on tool name; the script itself defaults to
deny, so it must run on every Bash call. Do not add an `if:`
prefilter — `if` controls only whether the hook runs, and a
non-matching command would then fall through to permission rules
instead of being denied.

**The one exception — a note arriving by quoted heredoc.** A `route`
verdict carries a note, and a note is prose: spaces, apostrophes,
backticks and parentheses all belong in it, and none of them survives
the class above. Quoting prose as an argument is itself the hazard —
the first incremental slice lost notes to backtick substitution that
way, and `route` now takes a note no other way. So a second shape is
allowed whole: a first line matching the grammar above and ending
`<<'DELIM'`, then a body closed by a final `DELIM` line. The delimiter
is quoted, so bash expands nothing in the body, and that is what makes
an arbitrary body safe to permit. The body is data; the only thing that
could turn it back into instructions is the delimiter line appearing
before the end, so that is forbidden, as is anything following the
terminator.

`DELIM` is whatever the opener names — letters, digits and underscores —
and every rule above re-anchors to that captured token. A fixed
delimiter would be a word a note may never contain, and a note is prose
about a corpus whose vocabulary nobody controls; the writer picking a
word their own note does not contain buys the same guarantee without the
exclusion.

The class is narrower than it strictly has to be. Single quotes would
make any byte but the quote itself inert, since bash expands nothing in
a heredoc delimiter word and quote removal returns it verbatim, so what
the hook captures is what bash matches — a differential test over `$(…)`,
backticks, `>`, `|`, `&&`, `*`, `\` and `"` found no delimiter on which
the two parses disagree. But that makes the hook's safety a proof about
quoting rather than something visible in the pattern, resting entirely
on one excluded character. Nobody needs a `$` in a delimiter, so the
narrow class costs nothing.

An empty body is refused as well. A heredoc carrying nothing is
malformed whichever verdict it accompanies: it opened a note and then
supplied none. That is a fact about the command's shape, so the hook
owns it — while *which* verdicts want a note stays a domain rule in
`record_verdict`.
`route` rejects an empty stdin for the same reason, so a caller
reaching the CLI by some other path cannot record an empty note
either.

**Write-path scope — `tools/ghmem/hooks/scripts/scope-docs-design.sh`,
synthesis agent only.** Matcher `Write|Edit`. Reads `.tool_input.file_path` and
exits 0 only when it resolves under `docs/design/`, else exits 2.
There is no frontmatter field for directory scoping, and permission
rules (`Edit(docs/design/**)`) only auto-approve rather than
hard-deny — and a `Write(…)` rule is silently unenforced — so a hook
is the only hard guarantee. Triage has no file-write tool and needs no
such hook.

## Bootstrap — already built, verify only

The cold start of an empty system is handled by mechanisms already in
the corpus/dispatch layers and hardened by the first live run's lead
finding. Nothing here is authored or committed as a seed; a
hand-written `INDEX.md` seed would violate the invariant that every
`INDEX.md` in git history is a `render_index` output.

- Missing `docs/design/` → `paths.design_dir()` creates it on demand
  (`mkdir(parents=True, exist_ok=True)`).
- Empty routing state → `build_index` finds zero docs and zero
  topics; `render_index` returns the header-only `INDEX.md` (its
  `if not summary.entries` branch).
- Missing `routing.jsonl` → `Ledger.__iter__` treats a non-existent
  file as empty; the first `ghmem route` creates the file and its
  parent via `Ledger.append`.

So the cold-start chain runs clean: `sync` → `rebuild` → `index`
(materializes `docs/design/` + header-only INDEX) → `triage` (first
routes create topics, mark dirty) → `synthesize` → `close`
(regenerates INDEX). Verifying this end to end is a Done criterion,
not a build task.

## Plugin extraction — near-future track, not built here

The two agent definitions and the hooks are the body of a future
Claude Code plugin; siblings (edify, cwd-safety, handoff) already run
in-tree plugins via a `plugin/` directory plus a `.bin/claude` shim
that injects `--plugin-dir`. This subproject builds the hacks for
early feedback, but authors them relocatable so extraction is a move,
not a rewrite: no absolute paths, hook commands via
`${CLAUDE_PROJECT_DIR}` (later `${CLAUDE_PLUGIN_ROOT}`), corpus and
design paths already resolved through `paths`.

One migration cost is known and bounded: **frontmatter `hooks:` are
ignored for plugin subagents.** At extraction the two frontmatter
hooks move to `settings.json`-style hooks that branch on the
`agent_type` stdin field (which carries the agent name) to apply the
allowlist only to the ghmem agents. Recorded here so it is not a
surprise; it changes nothing built now.

The name `ghmem` stays. It is collision-free (unregistered on PyPI as
both `ghmem` and `ghmem-cli`; no real GitHub repo), self-documenting
(`gh` + `mem`), and the incumbent — the swept alternatives were all
either occupied in-category or unfamiliar. Revisit at extraction if
desired, but the default is to keep it.

## Done criteria

Mechanical correctness against the live corpus, not routing accuracy:

1. **Cold-start chain.** `ghmem index` on an empty routing state
   produces the header-only `INDEX.md` and creates `docs/design/`
   with no traceback; the full `sync → rebuild → index → triage →
   synthesize → close` chain runs clean.
2. **Triage.** The agent completes a real batch: every reply is a
   well-formed `ghmem route` the CLI accepts, and the run terminates
   on the correct status line (`batch complete` vs `triage
   complete`). `routing.jsonl` gains valid events; live and rebuilt
   routing tables agree.
3. **Synthesis.** The agent turns one real dirty topic into a
   `docs/design/<name>.md` with parseable frontmatter and citations
   that resolve against the corpus; `ghmem close` accepts it and
   `INDEX.md` regenerates. The stale-token retry path is exercised
   deterministically — a route injected between `ghmem topic` and
   `ghmem close` forces the stale outcome.
4. **Hook.** The Bash allowlist demonstrably blocks a non-`ghmem`
   command and a chained `ghmem …; rm …` while allowing a bare
   `ghmem …`; the write-path hook blocks a `Write` outside
   `docs/design/`.

## References

- Parent spec: `docs/superpowers/specs/2026-07-09-ghmem-design.md`
  (§Agentic layer, §Validation, §Execution structure).
- Claude Code mechanics confirmed for the hook design:
  `https://code.claude.com/docs/en/sub-agents.md` (frontmatter
  `tools:`/`hooks:`, agent-scoped hooks),
  `https://code.claude.com/docs/en/hooks.md` (PreToolUse stdin/exit
  contract), `https://code.claude.com/docs/en/permissions.md`
  (blocking-hook precedence, `Edit`-vs-`Write` path matching).
