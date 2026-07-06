from typer.testing import CliRunner

from graphcheck import __version__
from graphcheck.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_no_hidden_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
