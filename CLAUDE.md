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
runs the whole gate: format (ruff fixes, ruff format, docformatter),
then ruff lint, docformatter `-c`, mypy strict, pytest, the bats hook
suites, and the 400-line file cap. `just precommit` is the same gate
under the name the plugin toolkit expects, and it is the one command
that must pass before a commit. `ruff`, `mypy` and `pytest` resolve
from the direnv-activated `.venv` on PATH.

The hooks live at `hooks/scripts/`, with their bats suites in `tests/`
beside the pytest ones. Each agent's frontmatter names its hook as
`${CLAUDE_PLUGIN_ROOT}/hooks/scripts/<script>`, and
`tests/test_plugin_layout.py` checks that every such command resolves
to an executable file inside the plugin and that each agent carries
exactly its pinned hooks. That matters because a hook path that does
not resolve exits 127, which reads as "not a block", so the agent would
run uncontained with nothing reported.

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
`scripts/`, `src/`, `tests/`, `hooks/`, `agents/` and `.claude-plugin/`
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
