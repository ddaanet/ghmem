# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Project goal

ghmem is an institutional-memory pipeline for a GitHub repository: it
fetches the repository's issue, PR, discussion and commit history into
a local corpus, has contained agents route each item to a topic, and
synthesizes one living design document per topic. `README.md` describes
what is built; `docs/ghmem-design.md` is the living design and the
source of truth for what is intended, with command contracts in
`docs/ghmem-harness.md`.

It is a Claude Code plugin (manifest at `.claude-plugin/plugin.json`,
agents at `agents/`, hooks at `hooks/scripts/`) wrapping a Python CLI
(`src/ghmem`), and it is developed with the claude-plugin-dev toolkit,
whose release flow depends on the `precommit` and `prerelease` recipes
in the `justfile`.

The corpus data never lives here. The CLI runs with the consumer
project as its working tree: `corpus_dir()` is `<git toplevel>/corpus`,
served triage packets go to `corpus/packets/`, topic docs to
`docs/design/`, and `corpus/target.toml` names the repository the
corpus mirrors. The data for `micro-editor/micro` is in the micro-craft
repo, the first consumer. Tests run on fixtures under `tests/fixtures/`
and never read a real corpus.

## Development loop

### Python toolchain

This is a uv-managed Python 3.14 package with a `justfile`. `just green`
runs the whole gate: format (ruff fixes, ruff format, docformatter, the
`requirements.txt` export), then ruff lint, docformatter `-c`, mypy
strict, pytest, the bats hook suites, and the 400-line file cap.
`just precommit` is the same gate under the name the plugin toolkit
expects, and it is the one command that must pass before a commit. `ruff`, `mypy` and `pytest` resolve
from the direnv-activated `.venv` on PATH.

The hooks live at `hooks/scripts/`, with their bats suites in `tests/`
beside the pytest ones. The scripts are thin wrappers that run
`src/ghmem/hook.py`, which holds the logic, with `python3 -I` from the
plugin's own tree, found from the script's own location. `hooks/hooks.json`
registers them for every session that enables the plugin, and each acts
only when the payload's `agent_type` names its agent
(`ghmem:ghmem-triage`, `ghmem:ghmem-synthesize`), passing every other
call through untouched.
Never put `hooks:` in an agent's frontmatter: Claude Code ignores it
for a plugin agent, saying so only in the debug log, and the agent runs
uncontained.

A hook fails open, silently — exit 0, nothing on stderr — only while it
does not know whose call it is: a payload that does not parse, an
`agent_type` that is absent, not a string or not its agent's, `python3`
missing from PATH, or the hook module missing from the tree. Once it has
read one of the two agents' names and then fails, it blocks with exit 2
and a reason on stderr. That stops only those agents, so it does not
brick the tools in every session that enables the plugin. Its one
dependency is `python3`, and `hooks/scripts/check-deps.sh`, the plugin's
`SessionStart` hook, names it when it is missing or cannot run the
module, at session start and again after resume, clear and compact.
`tests/test_plugin_layout.py` checks that
every registered command is the quoted
`"${CLAUDE_PLUGIN_ROOT}/hooks/scripts/<script>"` form resolving to an
executable inside the plugin — an unresolved path exits 127 and the
agent runs uncontained with nothing reported. The same file pins each
agent's `tools:` and runs the registered hooks against an out-of-scope
call for every one of them, so a tool no hook constrains for that agent
fails the suite.

`hooks/scripts/build-venv.sh`, the first `SessionStart` hook (timeout
300 s), builds the CLI's environment: `uv sync --frozen --no-dev` into
`.venv` in the plugin root, else `python3 -m venv` and `pip install` of
`requirements.txt` and then of the tree (`--no-deps`), which needs a
`python3` of 3.14 or newer and says so in `.venv-build.log` when it is
older. `bin/ghmem` then runs the result. It is silent when the environment's
`ghmem --version` already runs, and builds nothing when another `ghmem` is on
PATH ahead of the plugin's `bin/` (which comes last, so it would go unused):
a dogfood session in this checkout uses the direnv-activated `.venv` at the
project root. Its files are
`.venv/.built-by-ghmem-plugin`, the marker of an environment it built,
`.venv.lock/`, a lock directory (waited on up to 240 s, taken over once
ten minutes old), and `.venv-build.log`, the reason a build failed;
all three are git-ignored. A `.venv` without the marker, this checkout's own
direnv environment, is never removed. It is independent of the
containment hooks, which need only `python3`: a failed build never
changes what they do, and `tests/build-venv.bats` and
`tests/bin-ghmem.bats` pin the script and the launcher against stub
tools.

`requirements.txt`, at the repo root, is what that fallback installs.
The gate's format step generates it from `uv.lock` (`uv export`), so
never hand-edit it; it is one of the gate hash's inputs, and
`tests/test_plugin_layout.py` checks it pins every runtime dependency
and no dev-only one. It ships because the plugin is the repo tree.

A layout test proves the wiring, not the containment. After changing a
hook, an agent or `hooks.json`, run `just dogfood` and dispatch the
agent by `subagent_type` from a shim-launched session in a project
holding a `corpus/`: one command that must be blocked, one that must
be allowed, both allowed in the session's permissions so that only the
hook can refuse, and read the tool result rather than the agent's
report.

The gate is quiet when it passes — each check prints a labelled section
only if it had something to say — so it signs off with a one-line recap
of what passed uneventfully, and `✗ Red!` when something did not. Read
that line, not the silence: mypy in particular says nothing at all on
success (`error_summary = false`).

```
✓ ruff check · docformatter -c · mypy · <n> passed · <n> bats · line limits
```

The whole gate — formatting included, not just the tests — is cached
against a hash of its inputs, so a re-run over an untouched tree prints
`✓ Cached (inputs unchanged)` and does no work at all. The sentinel
lives at `tmp/.green-sentinel`; delete it to force a run.

The inputs are the interpreter version, the `docformatter` and `bats`
versions (the two tools `uv.lock` does not pin), and the name and
contents of every file under `justfile`, `pyproject.toml`, `uv.lock`,
`requirements.txt`, `scripts/`, `src/`, `tests/`, `hooks/`, `agents/` and
`.claude-plugin/`
— including files not yet `git add`-ed, so a brand-new test cannot hide
behind a stale pass. Editing a check invalidates the cache just as
editing a source file does. The hash is taken after formatting and
written only on a pass, so a failing run is never cached.

`just format` is not cached; it always runs.

`bats` is a system package, guarded the same way and for the same
reason.

**`docformatter` is the exception — it is not in the venv and cannot
be.** It has no Python 3.14 support, so it is not a dev dependency;
install it standalone with `uv tool install docformatter`, which puts
it in `~/.local/bin`. The justfile's `require-docformatter` guard
checks for it first — before formatting, and before `green` consults
its cache — and prints that reason, so a machine missing it fails with
an explanation rather than a bare `command not found`.

Running the gate without direnv needs both on PATH:
`PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH" just green` from the repo
root.

@memory/ddaanet/shared-claude.md
