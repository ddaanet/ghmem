"""Test CLI version option."""

from click.testing import CliRunner

from ghmem import __version__
from ghmem.cli import main


def test_version_flag_displays_version() -> None:
    """`--version` displays the version and program name.

    When: ghmem --version is called
    Then: Output contains version number and program name, exit code is 0
    """
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output
    assert "ghmem" in result.output.lower()
