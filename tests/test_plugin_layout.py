"""Test the plugin's layout: manifest version and containment hook wiring.

Each agent's containment is a PreToolUse hook registered by the plugin's own
`hooks/hooks.json`, whose script acts only when the payload's `agent_type`
names that agent. Claude Code ignores `hooks:` in a plugin agent's
frontmatter, so nothing an agent file says can contain it. A hook command that
does not resolve exits 127, which Claude Code reads as "not a block", so the
agent would run uncontained with nothing reported. These tests are what keeps
that from happening after a move or a rename.
"""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # no stubs; not a dependency to add

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def test_agents_are_exactly_the_pinned_set() -> None:
    """An agent definition that is not pinned here fails the suite.

    When: agents/ is listed
    Then: its files are the pinned agents and each `name:` is its file stem, so
        a new agent cannot ship without its containment being decided here
    """
    found = {path.stem for path in (PLUGIN_ROOT / "agents").glob("*.md")}

    assert found == set(EXPECTED_TOOLS)
    for agent in found:
        assert frontmatter(agent)["name"] == agent


def test_no_agent_declares_frontmatter_hooks() -> None:
    """An agent file never carries a containment claim the loader discards.

    When: each agent's frontmatter is read
    Then: it has no `hooks` key, since Claude Code drops that key for a plugin
        agent and a block left there would read as containment that is not
    """
    for agent in EXPECTED_TOOLS:
        assert "hooks" not in frontmatter(agent), agent


def test_each_agent_has_its_pinned_tools() -> None:
    """A tool cannot be handed to an agent without being pinned here.

    When: an agent's `tools:` are read
    Then: they are the pinned ones
    """
    for agent, expected in EXPECTED_TOOLS.items():
        tools = {tool.strip() for tool in frontmatter(agent)["tools"].split(",")}

        assert tools == expected, agent


def test_every_tool_an_agent_holds_is_refused_out_of_scope(tmp_path: Path) -> None:
    """No agent holds a tool that no hook constrains for that agent.

    When: the hooks registered for a tool receive a call from the agent that
        no scope allows — a non-`ghmem` command, a path outside the project
    Then: one of them blocks it, so a tool with a matcher but no script acting
        for that agent is caught
    """
    for agent, tools in EXPECTED_TOOLS.items():
        for tool in sorted(tools):
            payload = {
                "tool_name": tool,
                "agent_type": f"ghmem:{agent}",
                "cwd": str(tmp_path),
                "tool_input": {"command": "echo x", "file_path": "/etc/passwd"},
            }
            statuses = [
                subprocess.run(
                    [script_path(command)],
                    input=json.dumps(payload),
                    env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
                    capture_output=True,
                    text=True,
                    check=False,
                ).returncode
                for _, matcher, command in hook_commands()
                if tool in matcher.split("|")
            ]

            assert 2 in statuses, f"{agent} holds {tool} unconstrained: {statuses}"


def test_the_plugin_registers_exactly_its_pinned_hooks() -> None:
    """The plugin's hooks.json carries the pinned matcher-to-script hooks.

    When: the hook commands in hooks/hooks.json are read
    Then: they are the pinned (event, matcher, script) triples, no more and no
        fewer, so a dropped, swapped or duplicated hook is caught
    """
    actual = sorted(
        (event, matcher, script_path(command).name)
        for event, matcher, command in hook_commands()
    )

    assert actual == sorted(EXPECTED_HOOKS)


def test_hook_commands_resolve_to_executables_in_the_plugin() -> None:
    """Every hook command points at an executable file inside the plugin.

    When: `${CLAUDE_PLUGIN_ROOT}` is substituted with the repository root
    Then: each command is an existing, executable regular file under that root
    """
    commands = [command for _, _, command in hook_commands()]
    assert commands, "hooks/hooks.json registers no hooks"
    for command in commands:
        script = script_path(command)

        assert script.is_file(), f"{command} is not a file"
        assert os.access(script, os.X_OK), f"{command} not executable"
        assert script.resolve().is_relative_to(PLUGIN_ROOT), command


@pytest.mark.parametrize(
    "command",
    [
        '"${CLAUDE_PROJECT_DIR}/hooks/scripts/ghmem-only.sh"',
        '"${CLAUDE_PROJECT_DIR}/tools/ghmem/hooks/scripts/ghmem-only.sh"',
        '"$CLAUDE_PLUGIN_ROOT/hooks/scripts/ghmem-only.sh"',
        "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ghmem-only.sh",
        '"hooks/scripts/ghmem-only.sh"',
        "/usr/bin/true",
        '"${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ghmem-only.sh" ${HOME}',
        '"${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ghmem-only.sh"; true',
        '"${CLAUDE_PLUGIN_ROOT}/../outside.sh"',
    ],
)
def test_script_path_rejects_any_other_form(command: str) -> None:
    """Only a quoted `"${CLAUDE_PLUGIN_ROOT}/<path in the plugin>"` is accepted.

    When: a command uses another variable, an unquoted, bare or absolute path,
        extra shell, or climbs out of the plugin root
    Then: script_path refuses it rather than resolving it
    """
    with pytest.raises(ValueError, match="hook command"):
        script_path(command)


def test_manifest_version_matches_pyproject() -> None:
    """The plugin manifest and the Python package never drift apart.

    When: both versions are read
    Then: they are equal
    """
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert manifest.is_file(), f"{manifest} is missing"
    pyproject = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text())

    assert (
        json.loads(manifest.read_text())["version"] == pyproject["project"]["version"]
    )


EXPECTED_HOOKS = [
    ("PreToolUse", "Bash", "ghmem-only.sh"),
    ("PreToolUse", "Read", "scope-packets-only.sh"),
    ("PreToolUse", "Read|Write|Edit", "scope-docs-design.sh"),
]

# Which script acts for which agent is decided inside the scripts, on the
# payload's `agent_type`, and pinned by their bats suites.
EXPECTED_TOOLS = {
    "ghmem-triage": {"Bash", "Read"},
    "ghmem-synthesize": {"Bash", "Read", "Write", "Edit"},
}


def frontmatter(agent: str) -> dict[str, Any]:
    """Parse the YAML frontmatter block of `agents/<agent>.md`."""
    text = (PLUGIN_ROOT / "agents" / f"{agent}.md").read_text()
    match = re.match(r"---\n(.*?\n)---\n", text, re.DOTALL)
    assert match, f"{agent}: no frontmatter block"
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def hook_commands() -> list[tuple[str, str, str]]:
    """List `(event, matcher, command)` for every hook in hooks/hooks.json."""
    registry = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert registry.is_file(), f"{registry} is missing"
    found = []
    for event, entries in json.loads(registry.read_text())["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command", hook
                found.append((event, entry["matcher"], hook["command"]))
    return found


def script_path(command: str) -> Path:
    """Resolve a hook command to a path, accepting one strict form only.

    The form is `"${CLAUDE_PLUGIN_ROOT}/<plain relative path>"`, double quotes
    included: unquoted, a plugin installed under a path with a space in it
    splits into two words, the command is not found, and that exits 127.
    Anything else raises ValueError: another variable, a bare or absolute
    path, trailing arguments or shell, or a path that leaves the plugin root.
    """
    match = re.fullmatch(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([\w.-]+(/[\w.-]+)*)"', command)
    if not match:
        raise ValueError(f"unsupported hook command: {command!r}")
    path = PLUGIN_ROOT / match.group(1)
    if not path.resolve().is_relative_to(PLUGIN_ROOT):
        raise ValueError(f"hook command leaves the plugin root: {command!r}")
    return path
