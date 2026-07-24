from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.engine.runner import Engine, SuiteInput
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.neo4j_adapter import QueryResult
from graphcheck.telemetry.collector import TelemetryCollector
from graphcheck.telemetry.events import (
    CheckProcessed,
    EngineFaulted,
    QueryFinished,
    RunFinished,
    RunStarted,
    TargetProbeFinished,
)

TARGET = RunTarget(
    database="secret-database",
    server_version="5.18.7",
    edition="enterprise",
    fingerprint="sha256:secret-fingerprint",
    capabilities=Capabilities(apoc=False, count_store=True),
)
SUITE = """\
suite: private-suite
competency:
  - id: private-check
    question: Does a secret value exist?
    query: RETURN $secret AS value
    params: {secret: customer-secret}
    expect: {rows: {exactly: 1}}
"""


@dataclass
class Client:
    def run_read_result(self, query, params, *, timeout_s=None):
        return QueryResult([{"value": params["secret"]}], ("value",), ())


def test_engine_emits_ordered_reconciled_events_without_content():
    collector = TelemetryCollector()
    results = Engine(Client(), event_sink=collector).run(
        [SuiteInput.from_yaml(SUITE)],
        target=TARGET,
    )

    assert results.run.exit_code == 0
    assert [type(event) for event in collector.events] == [
        RunStarted,
        TargetProbeFinished,
        QueryFinished,
        CheckProcessed,
        RunFinished,
    ]
    assert [event.sequence for event in collector.events] == [1, 2, 3, 4, 5]
    terminal = collector.events[-1]
    assert terminal.query_count == 1
    assert terminal.selected_check_count == 1
    outbound = collector.posthog_events()
    completion = next(event for event in outbound if event.name == "graphcheck_run_completed")
    assert completion.properties["probe_ms"] == 0
    assert completion.properties["probe_outcome"] == "success"
    payload_text = repr([event.properties for event in outbound])
    for secret in (
        "secret-database",
        "secret-fingerprint",
        "private-suite",
        "private-check",
        "customer-secret",
        "RETURN $secret",
    ):
        assert secret not in payload_text
    assert "verdict" not in payload_text


def test_broken_sink_cannot_change_engine_results():
    class BrokenSink:
        def emit(self, event):
            raise RuntimeError("sink-secret")

    options = {
        "clock": lambda: datetime(2026, 7, 23, tzinfo=UTC),
        "monotonic": lambda: 1.0,
        "id_factory": lambda: "run-fixed",
    }
    expected = Engine(Client(), **options).run([SuiteInput.from_yaml(SUITE)], target=TARGET)
    actual = Engine(Client(), event_sink=BrokenSink(), **options).run(
        [SuiteInput.from_yaml(SUITE)],
        target=TARGET,
    )

    assert actual.model_dump() == expected.model_dump()


def test_engine_event_construction_failure_cannot_change_engine_results():
    collector = TelemetryCollector()
    options = {
        "clock": lambda: datetime(2026, 7, 23, tzinfo=UTC),
        "monotonic": lambda: 1.0,
        "id_factory": lambda: "run-fixed",
    }
    expected = Engine(Client(), **options).run([SuiteInput.from_yaml(SUITE)], target=TARGET)
    actual = Engine(
        Client(),
        event_sink=collector,
        telemetry_clock=lambda: datetime(2026, 7, 23),
        **options,
    ).run([SuiteInput.from_yaml(SUITE)], target=TARGET)

    assert actual.model_dump() == expected.model_dump()
    assert collector.events == ()


def test_unexpected_boundary_fault_emits_only_engine_fault_terminal():
    class FaultingEngine(Engine):
        def _results(self, **kwargs):
            raise MemoryError("customer path C:/private")

    collector = TelemetryCollector()
    with pytest.raises(MemoryError):
        FaultingEngine(Client(), event_sink=collector).run(
            [SuiteInput.from_yaml(SUITE)],
            target=TARGET,
        )

    terminals = [
        event for event in collector.events if isinstance(event, (RunFinished, EngineFaulted))
    ]
    assert len(terminals) == 1
    assert isinstance(terminals[0], EngineFaulted)
    assert terminals[0].exception_type.value == "MemoryError"
    assert "customer path" not in repr(collector.posthog_events())


def test_query_error_is_a_normal_check_and_run_terminal_not_an_engine_fault():
    class QueryErrorClient:
        def run_read_result(self, query, params, *, timeout_s=None):
            raise GraphCheckError(
                "neo4j.query_failed",
                "secret query and database failed",
                "secret fix",
            )

    collector = TelemetryCollector()
    results = Engine(QueryErrorClient(), event_sink=collector).run(
        [SuiteInput.from_yaml(SUITE)],
        target=TARGET,
    )

    assert results.checks[0].verdict.value == "errored"
    assert any(isinstance(event, RunFinished) for event in collector.events)
    assert not any(isinstance(event, EngineFaulted) for event in collector.events)
    query = next(event for event in collector.events if isinstance(event, QueryFinished))
    check = next(event for event in collector.events if isinstance(event, CheckProcessed))
    assert query.outcome.value == "error"
    assert query.error_code.value == "neo4j.query_failed"
    assert check.processing_outcome.value == "engine_error"
    assert "secret query" not in repr(collector.posthog_events())


def test_driver_timeout_retains_query_timeout_outcome():
    class QueryTimeoutClient:
        def run_read_result(self, query, params, *, timeout_s=None):
            raise GraphCheckTimeoutError(
                "neo4j.query_failed",
                "secret transaction timed out",
                "secret timeout fix",
            )

    collector = TelemetryCollector()
    Engine(QueryTimeoutClient(), event_sink=collector).run(
        [SuiteInput.from_yaml(SUITE)],
        target=TARGET,
    )

    query = next(event for event in collector.events if isinstance(event, QueryFinished))
    assert query.outcome.value == "timeout"
    assert query.error_code.value == "neo4j.query_failed"


def test_fail_fast_exposes_only_permitted_run_level_inference():
    suite = """\
suite: fail-fast-private
competency:
  - id: stopping-private-check
    question: Must be empty?
    query: RETURN 1 AS value
    expect: {empty: true}
  - id: later-private-check
    question: Would have run?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}}
"""

    class FailingClient:
        def run_read_result(self, query, params, *, timeout_s=None):
            return QueryResult(
                [
                    {
                        "value": 1,
                        "evidence": {
                            "kind": "node",
                            "id": "secret-node",
                            "labels": ["Secret"],
                        },
                    }
                ],
                ("value", "evidence"),
                (),
            )

    collector = TelemetryCollector()
    Engine(FailingClient(), event_sink=collector).run(
        [SuiteInput.from_yaml(suite)],
        target=TARGET,
        fail_fast=True,
    )

    started = next(event for event in collector.events if isinstance(event, RunStarted))
    terminal = next(event for event in collector.events if isinstance(event, RunFinished))
    checks = [event for event in collector.events if isinstance(event, CheckProcessed)]
    assert started.fail_fast_enabled is True
    assert terminal.early_stopped is True
    assert terminal.deadline_exhausted is False
    assert terminal.partial_reason_codes == ()
    assert checks[1].skip_reason.value == "not_run"
    payload = repr(collector.posthog_events())
    assert "stopping-private-check" not in payload
    assert "verdict" not in payload
