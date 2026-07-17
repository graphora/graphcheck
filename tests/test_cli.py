from pathlib import Path

from typer.testing import CliRunner

from graphcheck import __version__
from graphcheck.cli import app
from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
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


def test_profile_writes_baseline_and_prints_summary(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    baseline = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))

    class FakeClient:
        def close(self):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.cli.load_profiles", lambda root: object())
    monkeypatch.setattr(
        "graphcheck.cli.select_profile",
        lambda profiles, name: ("local", object()),
    )
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda selected: FakeClient())
    monkeypatch.setattr("graphcheck.cli.build_profile", lambda client: baseline)

    result = runner.invoke(app, ["profile"])

    paths = list((tmp_path / ".graphcheck" / "baselines").glob("*.json"))
    assert result.exit_code == 0
    assert len(paths) == 1
    for content in (
        "Profile completed.",
        "Status:",
        "Nodes:",
        "Relationships:",
        "Labels:",
        "Relationship Types:",
        "Constraints:",
        "Indexes:",
        "Baseline written to:",
    ):
        assert content in result.stdout


def test_profile_prints_partial_reason_and_summary(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    baseline = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    baseline = baseline.model_copy(
        update={
            "status": ProfileStatus.PARTIAL,
            "partial_reason": "test partial reason",
        }
    )

    class FakeClient:
        def close(self):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.cli.load_profiles", lambda root: object())
    monkeypatch.setattr(
        "graphcheck.cli.select_profile",
        lambda profiles, name: ("local", object()),
    )
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda selected: FakeClient())
    monkeypatch.setattr("graphcheck.cli.build_profile", lambda client: baseline)

    result = runner.invoke(app, ["profile"])

    paths = list((tmp_path / ".graphcheck" / "baselines").glob("*.json"))
    assert result.exit_code == 0
    assert len(paths) == 1
    assert "Status: partial" in result.stdout
    assert "Reason: test partial reason" in result.stdout
    for content in (
        "Nodes:",
        "Relationships:",
        "Labels:",
        "Relationship Types:",
        "Constraints:",
        "Indexes:",
        "Baseline written to:",
    ):
        assert content in result.stdout


def test_baseline_set_selects_previous_and_prints_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    directory.mkdir(parents=True)
    (directory / "20260714T120000.json").write_text("{}", encoding="utf-8")
    (directory / "20260714T143522.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["baseline", "set"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Baseline set to 20260714T120000.json"


def test_baseline_set_specific_snapshot_and_missing_snapshot_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    directory = tmp_path / ".graphcheck" / "baselines"
    directory.mkdir(parents=True)
    (directory / "20260714T120000.json").write_text("{}", encoding="utf-8")

    selected = runner.invoke(app, ["baseline", "set", "20260714T120000.json"])
    missing = runner.invoke(app, ["baseline", "set", "20260714T143522.json"])

    assert selected.exit_code == 0
    assert selected.stdout.strip() == "Baseline set to 20260714T120000.json"
    assert missing.exit_code == 1
    assert "baseline.not_found" in missing.output


def test_diff_identical_targets_do_not_prompt_and_print_no_drift(monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current, latest: (fixture, fixture),
    )

    result = runner.invoke(app, ["diff"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "No drift detected."
    assert "Do you want to continue?" not in result.stdout


def test_diff_explicit_snapshots_print_all_drift_messages(monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    resolved_arguments = []

    def resolve(current, latest):
        resolved_arguments.append((current, latest))
        return fixture, fixture

    monkeypatch.setattr("graphcheck.cli.resolve_diff_baselines", resolve)
    monkeypatch.setattr(
        "graphcheck.cli.compare_baselines",
        lambda current, latest: ["Schema", "+ Label Customer"],
    )

    result = runner.invoke(app, ["diff", "current.json", "latest.json"])

    assert result.exit_code == 0
    assert resolved_arguments == [("current.json", "latest.json")]
    assert result.stdout == "Graph drift detected.\n\nSchema\n+ Label Customer\n"


def test_diff_different_targets_yes_continues(tmp_path, monkeypatch):
    current_path, latest_path = _different_target_baselines(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current, latest: (current_path, latest_path),
    )
    calls = []
    monkeypatch.setattr(
        "graphcheck.cli.compare_baselines",
        lambda current, latest: calls.append((current, latest)) or [],
    )

    for answer in ("y\n", "yes\n"):
        calls.clear()
        result = runner.invoke(app, ["diff"], input=answer)
        assert result.exit_code == 0
        assert "WARNING" in result.stdout
        assert "Do you want to continue? [y/N]" in result.stdout
        assert "No drift detected." in result.stdout
        assert len(calls) == 1


def test_diff_different_targets_no_or_enter_cancels(tmp_path, monkeypatch):
    current_path, latest_path = _different_target_baselines(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current, latest: (current_path, latest_path),
    )
    calls = []
    monkeypatch.setattr(
        "graphcheck.cli.compare_baselines",
        lambda current, latest: calls.append((current, latest)),
    )

    for answer in ("n\n", "\n"):
        result = runner.invoke(app, ["diff"], input=answer)
        assert result.exit_code == 0
        assert "Diff cancelled by user." in result.stdout
    assert calls == []


def _different_target_baselines(tmp_path):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    current = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    latest = current.model_copy(
        update={"target": current.target.model_copy(update={"database": "another-database"})}
    )
    current_path = tmp_path / "current.json"
    latest_path = tmp_path / "latest.json"
    current_path.write_text(current.model_dump_json(by_alias=True), encoding="utf-8")
    latest_path.write_text(latest.model_dump_json(by_alias=True), encoding="utf-8")
    return current_path, latest_path
