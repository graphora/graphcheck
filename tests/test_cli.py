import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphcheck import __version__
from graphcheck.cli import app
from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Counts, DebugTrace, SupportVersions, Visibility
from graphcheck.packs.catalog import PACKS_DIRECTORY
from graphcheck.project import write_default_project

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain_terminal_text(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value)


def _baseline_fixture() -> BaselineProfile:
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    return BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))


def _configure_profile_command(tmp_path, monkeypatch, baseline: BaselineProfile):
    class RecordingClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = RecordingClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.cli.load_profiles", lambda root: object())
    monkeypatch.setattr(
        "graphcheck.cli.select_profile",
        lambda profiles, name: ("local", object()),
    )
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda selected: client)
    monkeypatch.setattr(
        "graphcheck.cli.build_profile",
        lambda selected_client, **kwargs: baseline,
    )
    return client


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


def test_mcp_serve_without_extra_prints_install_command(monkeypatch):
    real_import = __import__("importlib").import_module
    monkeypatch.setattr(
        "graphcheck.cli.import_module",
        lambda name: (
            (_ for _ in ()).throw(ModuleNotFoundError(name="mcp"))
            if name == "graphcheck.mcp.server"
            else real_import(name)
        ),
    )

    result = runner.invoke(app, ["mcp", "serve"])

    assert result.exit_code == 2
    assert "graphcheck mcp serve" in result.output
    assert 'pip install "graphcheck[mcp]"' in result.output


def test_init_writes_project_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "graphcheck.yml").exists()
    assert (tmp_path / "profiles.yml").exists()
    assert (tmp_path / "checks" / "example.yml").exists()
    assert (tmp_path / ".graphcheck").is_dir()
    assert "Next: edit checks/example.yml" in result.stdout


def test_init_reports_connection_error_details(tmp_path, monkeypatch):
    from graphcheck.errors import GraphCheckError

    def fail_init_trace(profile_name, profile):
        raise GraphCheckError(
            "neo4j.auth_failed",
            "Neo4j rejected the configured credentials.",
            "Edit profiles.yml with the password from Neo4j Desktop.",
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", fail_init_trace)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Neo4j was not detected: neo4j.auth_failed" in result.stdout
    assert "Neo4j rejected the configured credentials." in result.stdout
    assert "Fix: Edit profiles.yml" in result.stdout


def test_debug_json_reports_profile_error(tmp_path, monkeypatch):
    from graphcheck.errors import GraphCheckError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.find_project_root",
        lambda: (_ for _ in ()).throw(
            GraphCheckError(
                "project.missing",
                "No graphcheck.yml found.",
                "Run `graphcheck init` first.",
            )
        ),
    )

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "project.missing"' in result.stdout


def test_debug_wrong_uri_scheme_names_fix_without_traceback(tmp_path, monkeypatch):
    write_default_project(tmp_path)
    (tmp_path / "profiles.yml").write_text(
        "default: local\nprofiles:\n  local:\n    uri: http://localhost:7474\n"
        "    user: neo4j\n    password: pw\n    database: neo4j\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["debug"])

    assert result.exit_code == 1
    assert "profile.uri_invalid" in result.output
    assert "Fix:" in result.output
    assert "neo4j+s://" in result.output
    assert "Traceback" not in result.output


def test_debug_unexpected_failure_is_structured_without_traceback(tmp_path, monkeypatch):
    write_default_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.load_profiles", lambda root: 1 / 0)

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 1
    assert '"code": "debug.internal_error"' in result.stdout
    assert '"fix":' in result.stdout
    assert "Traceback" not in result.stdout


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
        versions=SupportVersions(
            graphcheck=__version__,
            neo4j_driver="6.2.0",
            neo4j_server="5.18.0",
            cypher="5",
        ),
    )


def _trace_without_read():
    return DebugTrace(
        profile="local",
        target=RunTarget(
            database="neo4j",
            server_version="5.18.0",
            edition="enterprise",
            fingerprint="abc123",
            capabilities=Capabilities(apoc=True, count_store=True),
        ),
        visibility=Visibility(can_connect=True, can_read=False, can_show_procedures=True),
        counts=Counts(nodes=None, relationships=None),
    )


def _trace_without_apoc():
    return DebugTrace(
        profile="local",
        target=RunTarget(
            database="neo4j",
            server_version="5.18.0",
            edition="enterprise",
            fingerprint="abc123",
            capabilities=Capabilities(apoc=False, count_store=True),
        ),
        visibility=Visibility(can_connect=True, can_read=True, can_show_procedures=True),
        counts=Counts(nodes=3, relationships=4),
    )


def _write_apoc_pack(path: Path) -> Path:
    source = (PACKS_DIRECTORY / "core.yml").read_text(encoding="utf-8")
    updated = source.replace("    requires: [read]", "    requires: [read, apoc]", 1)
    assert updated != source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return path


def test_init_reports_detected_neo4j(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Detected Neo4j at bolt://localhost:7687 (version 5.18.0)" in result.stdout
    assert "APOC: yes" in result.stdout


def test_debug_json_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"server_version": "5.18.0"' in result.stdout
    assert '"apoc": true' in result.stdout
    assert '"blocked_checks": []' in result.stdout
    assert '"nodes": 3' in result.stdout


def test_debug_human_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    monkeypatch.setattr("graphcheck.cli.debug_trace", lambda profile_name, profile: _trace())

    result = runner.invoke(app, ["debug"])

    assert result.exit_code == 0
    assert f"GraphCheck version: {__version__}" in result.stdout
    assert "Neo4j Python driver: 6.2.0" in result.stdout
    assert "Neo4j Server: 5.18.0" in result.stdout
    assert "Cypher: 5" in result.stdout
    assert "Edition: enterprise" in result.stdout
    assert "Database name: neo4j" in result.stdout
    assert "APOC: yes" in result.stdout
    assert "Credentials can see: connect, read, procedures" in result.stdout
    assert "Credentials cannot see: none detected" in result.stdout
    assert "Blocked checks: none" in result.stdout
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
    monkeypatch.setattr(
        "graphcheck.cli.build_profile",
        lambda client, *, telemetry_observer=None, telemetry_result_observer=None: baseline,
    )

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
        "Degree Distribution:",
        "Account: median=1.0, p95=3.0, p99=4.0, maximum=4",
        "Customer: median=1.0, p95=3.0, p99=4.0, maximum=4",
        "Property Coverage:",
        "Account.id (node): 100.0%",
        "Customer.id (node): 100.0%",
        "Customer.name (node): 66.67%",
        "Baseline written to:",
    ):
        assert content in result.stdout


def test_profile_json_prints_complete_baseline_without_human_summary(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        "graphcheck.cli.build_profile",
        lambda client, *, telemetry_observer=None, telemetry_result_observer=None: baseline,
    )

    result = runner.invoke(app, ["profile", "--json"])

    assert result.exit_code == 0
    assert result.stdout.strip() == baseline.model_dump_json(indent=2, by_alias=True)
    assert "Profile completed." not in result.stdout
    assert "Baseline written to:" not in result.stdout


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
    monkeypatch.setattr(
        "graphcheck.cli.build_profile",
        lambda client, *, telemetry_observer=None, telemetry_result_observer=None: baseline,
    )

    result = runner.invoke(app, ["profile"])

    paths = list((tmp_path / ".graphcheck" / "baselines").glob("*.json"))
    assert result.exit_code == 0
    assert len(paths) == 1
    assert "Profile completed with partial data." in result.stdout
    assert "Status: partial" in result.stdout
    assert "Reason: test partial reason" in result.stdout
    assert "Collected: 13 nodes, 7 relationships" in result.stdout
    assert "Baseline written to:" in result.stdout
    for content in (
        "Nodes:",
        "Relationships:",
        "Labels:",
        "Relationship Types:",
        "Constraints:",
        "Indexes:",
        "Degree Distribution:",
        "Property Coverage:",
    ):
        assert content not in result.stdout


def test_profile_handles_graphcheck_error(tmp_path, monkeypatch):
    _configure_profile_command(tmp_path, monkeypatch, _baseline_fixture())

    def fail_profile(client, *, telemetry_observer=None, telemetry_result_observer=None):
        raise GraphCheckError(
            "neo4j.query_failed",
            "Profiling query failed.",
            "Retry the profile command.",
        )

    monkeypatch.setattr("graphcheck.cli.build_profile", fail_profile)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 1
    assert "neo4j.query_failed" in result.output
    assert "Profiling query failed." in result.output


def test_profile_closes_client_after_success(tmp_path, monkeypatch):
    client = _configure_profile_command(tmp_path, monkeypatch, _baseline_fixture())

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert client.closed is True


def test_profile_closes_client_when_profiling_fails(tmp_path, monkeypatch):
    client = _configure_profile_command(tmp_path, monkeypatch, _baseline_fixture())

    def fail_profile(
        selected_client,
        *,
        telemetry_observer=None,
        telemetry_result_observer=None,
    ):
        raise GraphCheckError("neo4j.query_failed", "Profiling query failed.", "Retry.")

    monkeypatch.setattr("graphcheck.cli.build_profile", fail_profile)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 1
    assert client.closed is True


@pytest.mark.parametrize("telemetry_enabled", [False, True], ids=["disabled", "enabled"])
def test_profile_uses_stable_telemetry_signature(
    tmp_path,
    monkeypatch,
    isolated_telemetry_config,
    telemetry_enabled,
):
    from graphcheck.telemetry.policy import enable_telemetry

    baseline = _baseline_fixture()
    _configure_profile_command(tmp_path, monkeypatch, baseline)
    observed = {}
    if telemetry_enabled:
        enable_telemetry(path=isolated_telemetry_config)
    monkeypatch.setattr(
        "graphcheck.telemetry.posthog.HttpPostHogTransport",
        lambda *args, **kwargs: pytest.fail("real telemetry transport was constructed"),
    )

    def build(
        client,
        *,
        telemetry_observer=None,
        telemetry_result_observer=None,
    ):
        observed["observers"] = telemetry_observer, telemetry_result_observer
        return baseline

    monkeypatch.setattr("graphcheck.cli.build_profile", build)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert tuple(observer is not None for observer in observed["observers"]) == (
        telemetry_enabled,
        telemetry_enabled,
    )


def test_external_consent_file_cannot_affect_profile_tests(tmp_path):
    from graphcheck.telemetry.policy import enable_telemetry

    external_config = tmp_path / "external" / "telemetry.json"
    consent = enable_telemetry(path=external_config)
    environment = os.environ.copy()
    environment["GRAPHCHECK_TELEMETRY_CONFIG"] = str(external_config)
    environment.pop("GRAPHCHECK_TELEMETRY", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_cli.py::test_profile_uses_stable_telemetry_signature[disabled]",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "subprocess-pytest"),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert external_config.read_text(encoding="utf-8").find(str(consent.distinct_id)) >= 0


def test_profile_json_prints_partial_profile_without_human_summary(tmp_path, monkeypatch):
    baseline = _baseline_fixture().model_copy(
        update={
            "status": ProfileStatus.PARTIAL,
            "partial_reason": "degree collection timed out",
        }
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile", "--json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["status"] == "partial"
    assert payload["partial_reason"] == "degree collection timed out"
    assert "Profile completed." not in result.stdout


def test_profile_summary_handles_empty_labels(tmp_path, monkeypatch):
    baseline = _baseline_fixture()
    baseline = baseline.model_copy(
        update={"graph_schema": baseline.graph_schema.model_copy(update={"labels": []})}
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "Labels: 0" in result.stdout


def test_profile_summary_handles_empty_relationship_types(tmp_path, monkeypatch):
    baseline = _baseline_fixture()
    baseline = baseline.model_copy(
        update={"graph_schema": baseline.graph_schema.model_copy(update={"relationship_types": []})}
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "Relationship Types: 0" in result.stdout


def test_profile_summary_handles_empty_constraints(tmp_path, monkeypatch):
    baseline = _baseline_fixture()
    baseline = baseline.model_copy(
        update={"graph_schema": baseline.graph_schema.model_copy(update={"constraints": []})}
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "Constraints: 0" in result.stdout


def test_profile_summary_handles_empty_indexes(tmp_path, monkeypatch):
    baseline = _baseline_fixture()
    baseline = baseline.model_copy(
        update={"graph_schema": baseline.graph_schema.model_copy(update={"indexes": []})}
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "Indexes: 0" in result.stdout


def test_profile_summary_handles_empty_property_coverage(tmp_path, monkeypatch):
    baseline = _baseline_fixture()
    baseline = baseline.model_copy(
        update={"statistics": baseline.statistics.model_copy(update={"property_coverage": []})}
    )
    _configure_profile_command(tmp_path, monkeypatch, baseline)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "Property Coverage:" in result.stdout
    assert "Account.id (node):" not in result.stdout


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


def test_baseline_set_reports_error_when_no_baselines_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.baselines.find_project_root", lambda: tmp_path)
    (tmp_path / ".graphcheck" / "baselines").mkdir(parents=True)

    result = runner.invoke(app, ["baseline", "set"])

    assert result.exit_code == 1
    assert "baseline.missing" in result.output


def test_diff_identical_targets_do_not_prompt_and_print_no_drift(monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current, latest: (fixture, fixture),
    )

    result = runner.invoke(app, ["diff"])

    assert result.exit_code == 0
    assert result.stdout.strip() == (
        "diff  baseline.json → baseline.json\nfingerprint: MATCH\n\nNo drift detected."
    )
    assert "Do you want to continue?" not in result.stdout


def test_diff_structurally_invalid_json_baseline_exits_through_usage_error(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    valid = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"

    result = runner.invoke(app, ["diff", str(invalid), str(valid)])

    assert result.exit_code == 2
    assert "baseline.invalid: Baseline JSON root must be an object." in result.output
    assert not isinstance(result.exception, AttributeError)


@pytest.mark.parametrize(
    ("current_status", "latest_status"),
    [
        (ProfileStatus.PARTIAL, ProfileStatus.COMPLETE),
        (ProfileStatus.COMPLETE, ProfileStatus.PARTIAL),
        (ProfileStatus.PARTIAL, ProfileStatus.PARTIAL),
    ],
)
def test_diff_partial_baselines_are_inconclusive_before_target_prompt(
    tmp_path,
    monkeypatch,
    current_status,
    latest_status,
):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    baseline = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    current = baseline.model_copy(
        update={
            "status": current_status,
            "partial_reason": "collection timed out"
            if current_status is ProfileStatus.PARTIAL
            else None,
        }
    )
    latest = baseline.model_copy(
        update={
            "status": latest_status,
            "partial_reason": "collection timed out"
            if latest_status is ProfileStatus.PARTIAL
            else None,
            "target": baseline.target.model_copy(update={"database": "another-database"}),
        }
    )
    current_path = tmp_path / "current.json"
    latest_path = tmp_path / "latest.json"
    current_path.write_text(current.model_dump_json(by_alias=True), encoding="utf-8")
    latest_path.write_text(latest.model_dump_json(by_alias=True), encoding="utf-8")
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current_name, latest_name: (current_path, latest_path),
    )
    calls = []
    monkeypatch.setattr(
        "graphcheck.cli.compare_baselines",
        lambda current_baseline, latest_baseline: calls.append((current_baseline, latest_baseline)),
    )

    result = runner.invoke(app, ["diff"])

    assert result.exit_code == 2
    assert "diff.partial_baseline" in result.output
    assert "Comparison is inconclusive because one or more baselines are partial." in result.output
    assert "Generate complete baseline profiles" in result.output
    assert "Do you want to continue?" not in result.output
    assert "WARNING" not in result.output
    assert "drift detected" not in result.output.lower()
    assert calls == []


def test_diff_mutable_target_fields_do_not_trigger_identity_warning(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    current = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    latest = current.model_copy(
        update={
            "target": current.target.model_copy(
                update={
                    "fingerprint": "different-fingerprint",
                    "server_version": "5.19.0",
                    "edition": "enterprise",
                    "capabilities": current.target.capabilities.model_copy(
                        update={"apoc": not current.target.capabilities.apoc}
                    ),
                }
            )
        }
    )
    current_path = tmp_path / "current.json"
    latest_path = tmp_path / "latest.json"
    current_path.write_text(current.model_dump_json(by_alias=True), encoding="utf-8")
    latest_path.write_text(latest.model_dump_json(by_alias=True), encoding="utf-8")
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current_name, latest_name: (current_path, latest_path),
    )
    monkeypatch.setattr("graphcheck.cli.compare_baselines", lambda current, latest: [])

    result = runner.invoke(app, ["diff"])

    assert result.exit_code == 0
    assert "WARNING" not in result.stdout
    assert "Do you want to continue?" not in result.stdout


def test_diff_coverage_only_change_reports_drift_and_exits_one(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    current = BaselineProfile.model_validate_json(fixture.read_text(encoding="utf-8"))
    coverage = current.statistics.property_coverage
    latest = current.model_copy(
        update={
            "statistics": current.statistics.model_copy(
                update={
                    "property_coverage": [
                        coverage[0].model_copy(update={"coverage": 92.0}),
                        *coverage[1:],
                    ]
                }
            )
        }
    )
    current_path = tmp_path / "current.json"
    latest_path = tmp_path / "latest.json"
    current_path.write_text(current.model_dump_json(by_alias=True), encoding="utf-8")
    latest_path.write_text(latest.model_dump_json(by_alias=True), encoding="utf-8")
    monkeypatch.setattr(
        "graphcheck.cli.resolve_diff_baselines",
        lambda current_name, latest_name: (current_path, latest_path),
    )

    result = runner.invoke(app, ["diff"])

    assert result.exit_code == 1
    assert "fingerprint: MATCH" in result.stdout
    assert "Account.id cover    100.0% → 92.0% (-8.0 pp)" in result.stdout
    assert "No drift detected." not in result.stdout


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
        assert '"database"' in result.stdout
        assert '"server_version"' not in result.stdout
        assert '"edition"' not in result.stdout
        assert '"fingerprint"' not in result.stdout
        assert '"capabilities"' not in result.stdout
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


def test_diff_json_database_mismatch_exits_two_without_prompt(tmp_path, monkeypatch):
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

    result = runner.invoke(app, ["diff", "--json"])

    assert result.exit_code == 2
    assert "error: cannot diff baselines from different databases" in result.output
    assert "Do you want to continue?" not in result.output
    assert "WARNING" not in result.output
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


def test_report_open_opens_most_recent_html_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_default_project(tmp_path)
    older = tmp_path / ".graphcheck" / "runs" / "older" / "report.html"
    latest = tmp_path / ".graphcheck" / "runs" / "latest" / "report.html"
    older.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    older.write_text("older", encoding="utf-8")
    latest.write_text("latest", encoding="utf-8")
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(latest, ns=(2_000_000_000, 2_000_000_000))
    opened = []
    monkeypatch.setattr("graphcheck.cli.webbrowser.open", lambda url: opened.append(url) or True)

    result = runner.invoke(app, ["report", "--open"])

    assert result.exit_code == 0
    assert opened == [latest.resolve().as_uri()]
    assert f"Opened {latest}" in result.stdout


def test_report_open_honors_configured_artifacts_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "graphcheck.yml").write_text(
        "project: graphcheck\nchecks: checks\nartifacts: output\n", encoding="utf-8"
    )
    report = tmp_path / "output" / "runs" / "run-1" / "report.html"
    report.parent.mkdir(parents=True)
    report.write_text("report", encoding="utf-8")
    opened = []
    monkeypatch.setattr("graphcheck.cli.webbrowser.open", lambda url: opened.append(url) or True)

    result = runner.invoke(app, ["report", "--open"])

    assert result.exit_code == 0
    assert opened == [report.resolve().as_uri()]


def test_report_open_is_loud_when_no_report_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_default_project(tmp_path)
    monkeypatch.setattr(
        "graphcheck.cli.webbrowser.open",
        lambda url: (_ for _ in ()).throw(AssertionError("browser should not open")),
    )

    result = runner.invoke(app, ["report", "--open"])

    assert result.exit_code == 1
    assert "No report.html found" in result.stderr
    assert "Run `graphcheck run`" in result.stderr


def test_report_open_reports_browser_launch_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_default_project(tmp_path)
    report = tmp_path / ".graphcheck" / "runs" / "latest" / "report.html"
    report.parent.mkdir(parents=True)
    report.write_text("report", encoding="utf-8")
    monkeypatch.setattr("graphcheck.cli.webbrowser.open", lambda url: False)

    result = runner.invoke(app, ["report", "--open"])

    assert result.exit_code == 1
    assert "Could not open" in result.stderr


def test_report_without_open_explains_usage():
    result = runner.invoke(app, ["report"])

    assert result.exit_code == 0
    assert "graphcheck report --open" in result.stdout
    assert "graphcheck report --list" in result.stdout
    assert "graphcheck report --compare" in result.stdout
    assert "graphcheck report --prune" in result.stdout
    assert "graphcheck report --failures-only" in result.stdout


def test_report_help_describes_optional_open_id():
    result = runner.invoke(app, ["report", "--help"], color=True)

    assert result.exit_code == 0
    output = _plain_terminal_text(result.stdout)
    assert "Usage: graphcheck report [OPTIONS] [ID]" in output
    assert "Historical run ID to open; valid only with --open" in output


def test_report_run_option_has_been_replaced():
    result = runner.invoke(app, ["report", "--run", "run-one"], color=True)

    assert result.exit_code == 2
    output = _plain_terminal_text(result.stderr)
    assert "No such option" in output and "--run" in output


def test_debug_reports_checks_blocked_by_missing_read_access(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    monkeypatch.setattr(
        "graphcheck.cli.debug_trace", lambda profile_name, profile: _trace_without_read()
    )

    result = runner.invoke(app, ["debug"])

    assert result.exit_code == 0
    assert "Credentials cannot see: read" in result.stdout
    assert "Blocked checks:" in result.stdout
    assert "example/customer-name-present (completeness) requires read" in result.stdout
    assert "Grant read access" in result.stdout
    assert "Counts: unavailable (read access denied)" in result.stdout


def test_debug_json_reports_checks_blocked_by_missing_read_access(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    monkeypatch.setattr(
        "graphcheck.cli.debug_trace", lambda profile_name, profile: _trace_without_read()
    )

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0
    assert '"blocked_checks": [' in result.stdout
    assert '"check_id": "customer-name-present"' in result.stdout
    assert '"missing_capability": "read"' in result.stdout
    assert "Grant read access" in result.stdout


def test_debug_human_names_apoc_blocked_check_from_pack_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    pack_path = _write_apoc_pack(tmp_path / "pack-metadata" / "core.yaml")
    monkeypatch.setattr("graphcheck.packs.catalog.PACKS_DIRECTORY", pack_path.parent)
    monkeypatch.setattr(
        "graphcheck.cli.debug_trace", lambda profile_name, profile: _trace_without_apoc()
    )

    result = runner.invoke(app, ["debug"])

    assert result.exit_code == 0
    assert "APOC: no" in result.stdout
    assert "example/customer-name-present (completeness) requires apoc" in result.stdout
    assert "Install APOC" in result.stdout


def test_debug_json_names_apoc_blocked_check_from_pack_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.init_trace", lambda profile_name, profile: _trace())
    runner.invoke(app, ["init"])
    pack_path = _write_apoc_pack(tmp_path / "pack-metadata" / "core.yaml")
    monkeypatch.setattr("graphcheck.packs.catalog.PACKS_DIRECTORY", pack_path.parent)
    monkeypatch.setattr(
        "graphcheck.cli.debug_trace", lambda profile_name, profile: _trace_without_apoc()
    )

    result = runner.invoke(app, ["debug", "--json"])

    assert result.exit_code == 0
    assert '"check_id": "customer-name-present"' in result.stdout
    assert '"check": "completeness"' in result.stdout
    assert '"missing_capability": "apoc"' in result.stdout
    assert "Install APOC" in result.stdout
