import sys
import time
import urllib.request
from pathlib import Path

import pytest

from graphcheck import __version__
from graphcheck import cli as cli_module
from graphcheck.cli import _write_run_artifacts, cli
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.neo4j_adapter import QueryResult
from graphcheck.project import write_default_project
from graphcheck.reporting.writer import load_results
from graphcheck.telemetry.policy import enable_telemetry
from graphcheck.telemetry.posthog import PostHogAdapter

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"
TARGET = RunTarget(
    database="private-database",
    server_version="5.18.7",
    edition="enterprise",
    fingerprint="sha256:private-fingerprint",
    capabilities=Capabilities(apoc=False, count_store=True),
)


class RecordingTransport:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def send(self, event, properties):
        self.calls.append((event, dict(properties)))


class FakeClient:
    def __init__(self):
        self.closed = False

    def probe(self, *, timeout_s=None):
        return TARGET

    def run_read_result(self, query, params, *, timeout_s=None):
        return QueryResult([{"value": 1}], ("value",), ())

    def close(self):
        self.closed = True


@pytest.fixture
def recording_transport(tmp_path, monkeypatch):
    transport = RecordingTransport()

    def adapter_factory(consent, *, session=None, **kwargs):
        assert session is not None
        return PostHogAdapter(session, transport)

    monkeypatch.setenv("GRAPHCHECK_TELEMETRY", "1")
    monkeypatch.setenv(
        "GRAPHCHECK_TELEMETRY_CONFIG",
        str(tmp_path / "nonexistent-telemetry-consent.json"),
    )
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("GRAPHCHECK_POSTHOG_API_KEY", raising=False)
    monkeypatch.setattr(
        "graphcheck.telemetry.runtime.create_posthog_adapter",
        adapter_factory,
    )
    return transport


def _invoke_entrypoint(monkeypatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["graphcheck", *arguments])
    with pytest.raises(SystemExit) as raised:
        cli()
    return int(raised.value.code or 0)


def _project(tmp_path: Path, *, severity: str) -> None:
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    checks = tmp_path / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "finding.yml").write_text(
        f"""\
suite: private-suite
competency:
  - id: private-check
    severity: {severity}
    question: Is this private result empty?
    query: RETURN 1 AS value
    expect: {{empty: true}}
""",
        encoding="utf-8",
    )


def _command_event(transport: RecordingTransport) -> dict[str, object]:
    matches = [
        properties for name, properties in transport.calls if name == "graphcheck_command_completed"
    ]
    assert len(matches) == 1
    return matches[0]


def test_parse_time_error_emits_user_error_at_true_cli_boundary(
    monkeypatch,
    recording_transport,
):
    exit_code = _invoke_entrypoint(monkeypatch, "run", "--not-a-real-option")

    assert exit_code == 2
    command = _command_event(recording_transport)
    assert command["command"] == "run"
    assert command["process_outcome"] == "user_error"
    assert command["failure_stage"] == "config_load"
    assert command["telemetry_run_id"] is None
    assert "not-a-real-option" not in repr(command)


@pytest.mark.parametrize(
    ("severity", "expected_exit"),
    [("error", 1), ("warn", 2)],
)
def test_completed_nonzero_run_is_success_and_correlates_all_events(
    tmp_path,
    monkeypatch,
    recording_transport,
    severity,
    expected_exit,
):
    _project(tmp_path, severity=severity)
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "Neo4jClient", lambda profile: client)

    exit_code = _invoke_entrypoint(monkeypatch, "run")

    assert exit_code == expected_exit
    command = _command_event(recording_transport)
    assert command["process_outcome"] == "success"
    assert command["failure_stage"] is None
    assert command["telemetry_run_id"] is not None
    assert client.closed is True

    engine_events = [
        properties
        for name, properties in recording_transport.calls
        if name
        in {
            "graphcheck_run_started",
            "graphcheck_check_processed",
            "graphcheck_run_completed",
        }
    ]
    assert len(engine_events) == 3
    assert {properties["telemetry_command_id"] for properties in engine_events} == {
        command["telemetry_command_id"]
    }
    assert {properties["telemetry_run_id"] for properties in engine_events} == {
        command["telemetry_run_id"]
    }
    assert "verdict" not in repr(recording_transport.calls)
    assert "private-database" not in repr(recording_transport.calls)
    assert "private-check" not in repr(recording_transport.calls)


def test_profile_emits_dedicated_completion_with_probe_and_stage_timings(
    tmp_path,
    monkeypatch,
    recording_transport,
):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    baseline = BaselineProfile.model_validate_json(
        (FIXTURES / "baseline.json").read_text(encoding="utf-8")
    )
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "Neo4jClient", lambda profile: client)

    def profiled(
        client,
        *,
        telemetry_observer=None,
        telemetry_result_observer=None,
    ):
        assert telemetry_observer is not None
        assert telemetry_result_observer is not None
        telemetry_observer("probe", "success", 7, TARGET)
        telemetry_observer("degree_distribution", "success", 3, None)
        telemetry_observer("labels", "success", 10, None)
        telemetry_observer("relationship_types", "success", 2, None)
        telemetry_observer("constraints", "success", 1, None)
        telemetry_observer("indexes", "success", 1, None)
        telemetry_observer("property_coverage", "success", 4, None)
        telemetry_result_observer("complete", None, False)
        return baseline

    monkeypatch.setattr(cli_module, "build_profile", profiled)

    exit_code = _invoke_entrypoint(monkeypatch, "profile")

    assert exit_code == 0
    matches = [
        properties
        for name, properties in recording_transport.calls
        if name == "graphcheck_profile_completed"
    ]
    assert len(matches) == 1
    completion = matches[0]
    assert completion["outcome"] == "complete"
    assert completion["probe_outcome"] == "success"
    assert completion["probe_duration_ms"] == 7
    assert completion["schema_ms"] == 11
    assert completion["property_coverage_ms"] == 4
    assert completion["degree_distribution_ms"] == 3
    assert completion["last_completed_stage"] == "property_coverage"
    assert completion["server_version_major"] == 5
    assert completion["server_version_minor"] == 18
    assert client.closed is True
    command = _command_event(recording_transport)
    assert command["baseline_artifact"] == "written"
    assert completion["telemetry_command_id"] == command["telemetry_command_id"]


def test_profile_partial_uses_structured_reason_and_deadline_state(
    tmp_path,
    monkeypatch,
    recording_transport,
):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    baseline = BaselineProfile.model_validate_json(
        (FIXTURES / "baseline.json").read_text(encoding="utf-8")
    ).model_copy(
        update={
            "status": ProfileStatus.PARTIAL,
            "partial_reason": "private human diagnostic that must not be parsed or sent",
        }
    )
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "Neo4jClient", lambda profile: client)

    def profiled(
        client,
        *,
        telemetry_observer=None,
        telemetry_result_observer=None,
    ):
        assert telemetry_observer is not None
        assert telemetry_result_observer is not None
        telemetry_observer("probe", "success", 7, TARGET)
        telemetry_observer("labels", "timeout", 20, None)
        telemetry_result_observer("partial", "deadline_exhausted", True)
        return baseline

    monkeypatch.setattr(cli_module, "build_profile", profiled)

    exit_code = _invoke_entrypoint(monkeypatch, "profile")

    assert exit_code == 0
    completion = next(
        properties
        for name, properties in recording_transport.calls
        if name == "graphcheck_profile_completed"
    )
    assert completion["outcome"] == "partial"
    assert completion["partial_reason"] == "deadline_exhausted"
    assert completion["deadline_exhausted"] is True
    assert completion["last_completed_stage"] == "probe"
    assert "private human diagnostic" not in repr(recording_transport.calls)


def test_successful_profile_help_does_not_emit_profile_completion(
    monkeypatch,
    recording_transport,
):
    exit_code = _invoke_entrypoint(monkeypatch, "profile", "--help")

    assert exit_code == 0
    assert not any(name == "graphcheck_profile_completed" for name, _ in recording_transport.calls)
    assert _command_event(recording_transport)["process_outcome"] == "success"


def test_unexpected_profile_failure_is_classified_as_profile_collection(
    tmp_path,
    monkeypatch,
    recording_transport,
):
    write_default_project(tmp_path)
    write_default_profiles(tmp_path)
    client = FakeClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "Neo4jClient", lambda profile: client)

    def fail_profile(*args, **kwargs):
        raise RuntimeError("private unexpected profiler failure")

    monkeypatch.setattr(cli_module, "build_profile", fail_profile)
    monkeypatch.setattr(sys, "argv", ["graphcheck", "profile"])

    with pytest.raises(RuntimeError, match="private unexpected profiler failure"):
        cli()

    command = _command_event(recording_transport)
    assert command["process_outcome"] == "unexpected_error"
    assert command["failure_stage"] == "profile_collection"
    assert command["safe_error_code"] == "profile.collection_failed"
    completion = next(
        properties
        for name, properties in recording_transport.calls
        if name == "graphcheck_profile_completed"
    )
    assert completion["outcome"] == "error"
    assert completion["safe_error_code"] == "profile.collection_failed"
    assert "private unexpected profiler failure" not in repr(recording_transport.calls)
    assert client.closed is True


def test_profile_setup_failure_still_emits_dedicated_completion(
    tmp_path,
    monkeypatch,
    recording_transport,
):
    monkeypatch.chdir(tmp_path)

    exit_code = _invoke_entrypoint(monkeypatch, "profile")

    assert exit_code == 1
    matches = [
        properties
        for name, properties in recording_transport.calls
        if name == "graphcheck_profile_completed"
    ]
    assert len(matches) == 1
    completion = matches[0]
    assert completion["outcome"] == "error"
    assert completion["last_completed_stage"] is None
    assert completion["safe_error_code"] == "project.missing"


def test_report_render_failure_marks_requested_artifact_and_stage(
    tmp_path,
    monkeypatch,
    recording_transport,
):
    write_default_project(tmp_path)
    results = load_results(FIXTURES / "results.complete.json")
    _write_run_artifacts(results, tmp_path / ".graphcheck" / "runs")
    monkeypatch.chdir(tmp_path)

    def fail_render(*args, **kwargs):
        raise OSError("private render failure")

    monkeypatch.setattr(cli_module, "write_html_report", fail_render)

    exit_code = _invoke_entrypoint(monkeypatch, "report", "--failures-only")

    assert exit_code == 1
    command = _command_event(recording_transport)
    assert command["action"] == "failures-only"
    assert command["process_outcome"] == "unexpected_error"
    assert command["failure_stage"] == "report_render"
    assert command["report_artifact"] == "error"
    assert command["render_ms"] is not None
    assert "private render failure" not in repr(command)


@pytest.mark.parametrize(
    "network_error",
    [
        OSError("network disabled: private air-gapped host"),
        TimeoutError("private PostHog request timed out"),
        ConnectionError("private PostHog connection refused"),
    ],
    ids=["offline", "timeout", "connection-refused"],
)
def test_posthog_network_failure_is_silent_and_does_not_change_cli_behavior(
    tmp_path,
    monkeypatch,
    capsys,
    network_error,
):
    config = tmp_path / "telemetry.json"
    enable_telemetry(path=config)
    monkeypatch.setenv("GRAPHCHECK_TELEMETRY_CONFIG", str(config))
    monkeypatch.setenv("GRAPHCHECK_POSTHOG_API_KEY", "phc_test")
    calls = []

    def fail_network(*args, **kwargs):
        calls.append((args, kwargs))
        raise network_error

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    started = time.monotonic()
    exit_code = _invoke_entrypoint(monkeypatch, "--version")
    elapsed = time.monotonic() - started
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out.strip() == f"graphcheck {__version__}"
    assert captured.err == ""
    assert len(calls) == 1
    assert elapsed < 1.0
    assert str(network_error) not in captured.out
    assert str(network_error) not in captured.err
