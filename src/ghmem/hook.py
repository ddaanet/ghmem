"""Containment hooks for ghmem's two agents, run as `ghmem-hook <name>`.

The plugin registers these for every session that enables it, on every Bash,
Read, Write and Edit call, because Claude Code ignores `hooks:` in a plugin
agent's own frontmatter. Each acts only for the agents it names, read from
the payload's `agent_type`, and passes every other call through untouched.

This module imports `json`, `os`, `re` and `sys` to do its work, plus `typing`
and `collections.abc` for annotations (about 3 ms), and the
package `__init__` keeps `importlib.metadata` out of the way: the process
starts once per tool call, so its cost is paid by every session. It stays off
the `ghmem` click entry point for the same reason.

A hook that cannot tell whose call it is lets it through, silently, exit 0
with nothing on stderr: refusing would brick those tools in every session
that enables the plugin, and a non-2 exit with stderr shows as a hook error
on every call. That covers a payload that does not parse and a missing
`ghmem-hook`, which `check-deps.sh` reports at SessionStart and each
agent's opening containment check catches. Once the payload has named one of
the hook's agents the caller is known, and a hook that then fails blocks:
that stops those two agents only, and nothing after identification depends on
a tool that may be absent.
"""

import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

TRIAGE = "ghmem:ghmem-triage"
SYNTHESIZE = "ghmem:ghmem-synthesize"


def main() -> int:
    """Run the hook named by the first argument; return its exit status.

    Exit 2 with a reason on stderr blocks the call. The hook passes with a
    silent 0 until it knows the caller: an unknown name, a payload that does
    not parse and an `agent_type` that is not one of the hook's agents all
    end there. From the moment it has identified one of its agents it blocks
    instead, so a payload it then cannot read, or a path it cannot resolve,
    stops that agent's call and nothing else.
    """
    try:
        name = sys.argv[1]
        hook = HOOKS[name]
        payload = json.loads(sys.stdin.read())
        agent = payload["agent_type"]
        if not isinstance(agent, str) or agent not in hook.agents:
            return 0
    except Exception:  # noqa: BLE001 - the caller is unknown, so nothing to hold
        return 0
    try:
        reason = hook.check(payload)
    except Exception as error:  # noqa: BLE001 - an identified agent fails closed
        reason = (
            f"{name}: blocked — the call could not be checked ({type(error).__name__})"
        )
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


def check_bash(payload: dict[str, Any]) -> str | None:
    r"""Return a block reason unless the command is a sanctioned `ghmem` call.

    Positive character allowlist, not a metacharacter denylist: none of
    `; & | $ \` < > ( )` or newline is in the permitted class, so chaining,
    substitution, pipes and redirects are excluded by construction rather
    than enumerated.

    That class cannot carry a note, and a note is the reason `route` exists.
    So one shape is allowed past it whole: a heredoc with a quoted delimiter.
    Quoted means bash expands nothing in the body, which is what makes an
    arbitrary body safe to permit; the body is data, and the only thing that
    could turn it back into instructions is the delimiter line appearing
    early, so that is what the checks below forbid.

    The opener names its own delimiter rather than being held to a fixed
    word. A fixed one is a word a note may never contain, and a note is prose
    about a corpus whose vocabulary nobody controls; the writer picking a
    word their own note does not contain is the same guarantee without the
    exclusion. The delimiter is held to letters, digits and underscores:
    narrow enough to check at sight, and nobody needs more.

    An empty body is refused as well. A heredoc carrying nothing is malformed
    whichever verdict it accompanies: it opened a note and then supplied
    none. That is a fact about the command's shape, so it belongs here; which
    verdicts want a note is a domain rule and stays in `record_verdict`.
    """
    command = payload["tool_input"]["command"]
    if not isinstance(command, str):
        msg = "command is not a string"
        raise TypeError(msg)
    first, newline, rest = command.partition("\n")
    if not newline:
        return None if BARE.fullmatch(command) else BASH_BLOCK
    opener = OPENER.fullmatch(first)
    if opener is None:
        return BASH_BLOCK
    delim = opener.group(1)
    # One optional trailing newline is the shell's, not the body's.
    lines = rest.removesuffix("\n").split("\n")
    if lines[-1] != delim or delim in lines[:-1]:
        return BASH_BLOCK
    if not "".join(lines[:-1]).strip():
        return BASH_BLOCK
    return None


def check_packets(payload: dict[str, Any]) -> str | None:
    """Return a block reason unless the Read stays inside `corpus/packets/`.

    The one directory `packet-file:` lines can ever point at (see
    `paths.packet_dir`). Without this, the only thing standing between a
    corpus item's rendered GitHub text and an arbitrary-file Read is the
    agent's own judgment that a `packet-file:` line it sees is the genuine
    one `serve_next` spliced in, not a line some issue or comment body happens
    to contain. Corpus content is attacker-reachable, since anyone can file a
    GitHub issue, so that judgment is not a security boundary. This is:
    whatever path the agent was steered toward, Read still refuses anything
    outside the packet directory.
    """
    resolved, scope = resolve(payload, "corpus/packets")
    if resolved.startswith(scope + os.sep):
        return None
    return (
        "scope-packets-only: blocked — Read is scoped to corpus/packets/ only "
        f"({resolved} is outside {scope})"
    )


def check_design(payload: dict[str, Any]) -> str | None:
    """Return a block reason unless the call stays inside `docs/design/`.

    Read is held to the same directory as Write and Edit. The agent needs it
    only to open the doc it is about to rewrite, and it works from untrusted
    corpus text into docs that get committed: with Read unscoped, an injected
    instruction could have it copy any file on the machine into one.

    There is no frontmatter field for directory scoping, and permission rules
    only auto-approve rather than hard-deny (a Write(...) rule is silently
    unenforced), so this hook is the only hard guarantee.
    """
    resolved, scope = resolve(payload, "docs/design")
    if resolved == scope or resolved.startswith(scope + os.sep):
        return None
    return (
        "scope-docs-design: blocked — Read, Write and Edit are scoped to "
        f"docs/design/ only ({resolved} is outside {scope})"
    )


def resolve(payload: dict[str, Any], subdir: str) -> tuple[str, str]:
    """Return the call's `file_path` and `<project>/<subdir>`, both resolved.

    A relative `file_path` is taken from the payload's `cwd`. `os.path.realpath`
    resolves symlinks and tolerates components that do not exist, which is
    GNU `realpath -m`; macOS's `realpath` has no such mode, and this does not
    ask it.
    """
    file_path = payload["tool_input"]["file_path"]
    cwd = payload.get("cwd") or ""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    if not isinstance(file_path, str) or not project_dir:
        msg = "no file_path or project directory"
        raise TypeError(msg)
    if not file_path.startswith("/"):
        file_path = f"{cwd or project_dir}/{file_path}"
    return os.path.realpath(file_path), os.path.realpath(f"{project_dir}/{subdir}")


class Hook:
    """One containment hook: the agents it acts for and its decision."""

    def __init__(
        self,
        agents: frozenset[str],
        check: Callable[[dict[str, Any]], str | None],
    ) -> None:
        """Bind a decision to the `agent_type` values it applies to."""
        self.agents = agents
        self.check = check


TAB = "\t"
SEP = f"[ {TAB}]"
ARG = "[A-Za-z0-9_./:=-]+"
BARE = re.compile(f"ghmem(?:{SEP}{ARG})*")
OPENER = re.compile(f"ghmem(?:{SEP}{ARG})*{SEP}<<'([A-Za-z0-9_]+)'")

BASH_BLOCK = (
    "ghmem-only: blocked — allowed are a bare 'ghmem ...' invocation and "
    "one ending \"<<'DELIM'\" for a DELIM of your choosing (letters, digits "
    "and underscores), carrying a non-empty note body closed by a final "
    "DELIM line and naming it nowhere else "
    "(no chaining, substitution, pipes, or redirects)"
)

HOOKS = {
    "ghmem-only": Hook(frozenset({TRIAGE, SYNTHESIZE}), check_bash),
    # ghmem-synthesize's Read is the design hook's to hold, not this one's.
    "scope-packets-only": Hook(frozenset({TRIAGE}), check_packets),
    "scope-docs-design": Hook(frozenset({SYNTHESIZE}), check_design),
}
