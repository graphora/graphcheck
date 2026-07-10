from typer.testing import CliRunner

from graphcheck import __version__
from graphcheck.cli import app
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.neo4j_adapter import Counts, DebugTrace, Visibility

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_runs():
    # Smoke test only: --help exits cleanly and produces output. We deliberately do NOT
    # assert on the rendered help text — Typer/Rich wraps and styles it by terminal width,
    # which differs between local and CI. The --version option's behaviour is covered above.
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_init_writes_project_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "graphcheck.yml").exists()
    assert (tmp_path / "profiles.yml").exists()
    assert (tmp_path / "checks" / "example.yml").exists()
    assert (tmp_path / ".graphcheck").is_dir()
    assert "Next: edit checks/example.yml" in result.stdout


def test_init_reports_connection_error_details(tmp_path, monkeypatch):
    from graphcheck.errors import GraphCheckError

    def fail_debug_trace(profile_name, profile):
        raise GraphCheckError(
            "neo4j.auth_failed",
            "Neo4j rejected the configured credentials.",
            "Edit profiles.yml with the password from Neo4j Desktop.",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.debug_trace", fail_debug_trace)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Neo4j was not detected: neo4j.auth_failed" in result.stdout
    assert "Neo4j rejected the configured credentials." in result.stdout
    assert "Fix: Edit profiles.yml" in result.stdout


def test_debug_json_reports_profile_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "project.missing"' in result.stdout


def _trace():
    return DebugTrace(
        profile="local",
        target=RunTarget(
            database="neo4j",
            server_version="5.18.0",
            edition="enterprise",
            fingerprint="abc123",
            capabilities=Capabilities(apoc=True, count_store=True),
        ),
        visibility=Visibility(can_connect=True, can_read=True, can_show_procedures=True),
        counts=Counts(nodes=3, relationships=4),
    )


def test_init_reports_detected_neo4j(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Detected Neo4j at bolt://localhost:7687 (version 5.18.0)" in result.stdout


def test_debug_json_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"server_version": "5.18.0"' in result.stdout
    assert '"nodes": 3' in result.stdout


def test_debug_human_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["debug"])

    assert result.exit_code == 0
    assert "Neo4j version: 5.18.0" in result.stdout
    assert "Edition: enterprise" in result.stdout
    assert "Database name: neo4j" in result.stdout
    assert "Counts: 3 nodes, 4 relationships" in result.stdout
