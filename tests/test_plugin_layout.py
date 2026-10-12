"""Test the plugin's layout: manifest version and agent hook wiring.

Each agent's containment is a PreToolUse hook named in its frontmatter by path.
A path that does not resolve exits 127, which Claude Code reads as "not a
block", so the agent would run uncontained with nothing reported. These tests
are what keeps that from happening after a move or a rename.
"""

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # no stubs; not a dependency to add

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def test_agents_are_exactly_the_pinned_set() -> None:
    """An agent definition without a pinned hook mapping fails the suite.

    When: agents/ is listed
    Then: its files are the pinned agents and each `name:` is its file stem, so
        a new agent cannot ship without its containment being pinned here
    """
    found = {path.stem for path in (PLUGIN_ROOT / "agents").glob("*.md")}

    assert found == set(EXPECTED_HOOKS)
    for agent in found:
        assert frontmatter(agent)["name"] == agent


def test_each_agent_has_its_pinned_hooks() -> None:
    """Every agent carries exactly its pinned matcher-to-script hooks.

    When: the hook commands in an agent's frontmatter are read
    Then: they are the pinned (event, matcher, script) triples, no more and no
        fewer, so a dropped, swapped or duplicated hook is caught
    """
    for agent, expected in EXPECTED_HOOKS.items():
        actual = sorted(
            (event, matcher, Path(command).name)
            for event, matcher, command in hook_commands(frontmatter(agent))
        )

        assert actual == sorted(expected), agent


def test_hook_commands_resolve_to_executables_in_the_plugin() -> None:
    """Every hook command points at an executable file inside the plugin.

    When: `${CLAUDE_PLUGIN_ROOT}` is substituted with the repository root
    Then: each command is an existing, executable regular file under that root
    """
    for agent in EXPECTED_HOOKS:
        commands = [command for _, _, command in hook_commands(frontmatter(agent))]
        assert commands, f"{agent} has no hooks"
        for command in commands:
            try:
                script = script_path(command)
            except ValueError as exc:
                raise AssertionError(f"{agent}: {exc}") from exc

            assert script.is_file(), f"{agent}: {command} is not a file"
            assert os.access(script, os.X_OK), f"{agent}: {command} not executable"
            assert script.resolve().is_relative_to(PLUGIN_ROOT), command


@pytest.mark.parametrize(
    "command",
    [
        "${CLAUDE_PROJECT_DIR}/hooks/scripts/ghmem-only.sh",
        "${CLAUDE_PROJECT_DIR}/tools/ghmem/hooks/scripts/ghmem-only.sh",
        "$CLAUDE_PLUGIN_ROOT/hooks/scripts/ghmem-only.sh",
        "hooks/scripts/ghmem-only.sh",
        "/usr/bin/true",
        "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ghmem-only.sh ${HOME}",
        "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/ghmem-only.sh; true",
        "${CLAUDE_PLUGIN_ROOT}/../outside.sh",
    ],
)
def test_script_path_rejects_any_other_form(command: str) -> None:
    """Only `${CLAUDE_PLUGIN_ROOT}/<path inside the plugin>` is accepted.

    When: a command uses another variable, a bare or absolute path, extra
        shell, or climbs out of the plugin root
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


EXPECTED_HOOKS: dict[str, list[tuple[str, str, str]]] = {
    "ghmem-triage": [
        ("PreToolUse", "Bash", "ghmem-only.sh"),
        ("PreToolUse", "Read", "scope-packets-only.sh"),
    ],
    "ghmem-synthesize": [
        ("PreToolUse", "Bash", "ghmem-only.sh"),
        ("PreToolUse", "Write|Edit", "scope-docs-design.sh"),
    ],
}


def frontmatter(agent: str) -> dict[str, Any]:
    """Parse the YAML frontmatter block of `agents/<agent>.md`."""
    text = (PLUGIN_ROOT / "agents" / f"{agent}.md").read_text()
    match = re.match(r"---\n(.*?\n)---\n", text, re.DOTALL)
    assert match, f"{agent}: no frontmatter block"
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def hook_commands(front: dict[str, Any]) -> list[tuple[str, str, str]]:
    """List `(event, matcher, command)` for every hook in the frontmatter."""
    found = []
    for event, entries in front.get("hooks", {}).items():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command", hook
                found.append((event, entry["matcher"], hook["command"]))
    return found


def script_path(command: str) -> Path:
    """Resolve a hook command to a path, accepting one strict form only.

    The form is `${CLAUDE_PLUGIN_ROOT}/` followed by a plain relative path.
    Anything else raises ValueError: another variable, a bare or absolute
    path, trailing arguments or shell, or a path that leaves the plugin root.
    """
    prefix = "${CLAUDE_PLUGIN_ROOT}/"
    relative = command.removeprefix(prefix)
    if not command.startswith(prefix) or not re.fullmatch(
        r"[\w.-]+(/[\w.-]+)*", relative
    ):
        raise ValueError(f"unsupported hook command: {command!r}")
    path = PLUGIN_ROOT / relative
    if not path.resolve().is_relative_to(PLUGIN_ROOT):
        raise ValueError(f"hook command leaves the plugin root: {command!r}")
    return path
