import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from graphcheck import __version__
from graphcheck.contracts.results import (
    Capabilities,
    ResultsTarget,
    RunStatus,
    RunTarget,
    SkipReason,
    Verdict,
)
from graphcheck.engine.runner import Engine, EngineConfig, SuiteInput, YamlSuiteInput
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.neo4j_adapter import QueryResult
from graphcheck.packs import PACK_VERSION

TARGET = ResultsTarget(
    database="neo4j",
    server_version="5.18.0",
    edition="community",
    fingerprint="sha256:graph",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)
EMPTY_TARGET = TARGET.model_copy(update={"nodes": 0, "relationships": 0})

PASSING_SUITE = """\
suite: customer-quality
conformance:
  - id: customer-id-present
    check: completeness
    with: {label: Customer, property: id, threshold: 1.0}
"""

TWO_COMPETENCIES = """\
suite: competencies
competency:
  - id: first
    question: First query?
    query: "RETURN 1 AS value /* first */"
    expect: {rows: {exactly: 1}, columns: [value]}
  - id: second
    question: Second query?
    query: "RETURN 1 AS value /* second */"
    expect: {rows: {exactly: 1}, columns: [value]}
"""

ONE_COMPETENCY = """\
suite: competencies
competency:
  - id: first
    question: First query?
    query: "RETURN 1 AS value /* first */"
    expect: {rows: {exactly: 1}, columns: [value]}
"""

GENERATED_SUITE = """\
suite: customer-quality
generated: true
conformance:
  - id: customer-id-present
    check: completeness
    with: {label: Customer, property: id, threshold: 1.0}
"""

DRIFT_SUITE = """\
suite: drift-suite
drift:
  - id: customer-count
    metric: node_count
    target: {label: Customer}
    baseline: latest
    tolerance: {max_drop_pct: 10}
"""

DANGLING_SUITE = """\
suite: store-integrity
conformance:
  - id: dangling
    check: dangling_rels
    with: {rel_type: OWNS}
"""


@dataclass(frozen=True)
class RichResult:
    rows: list[dict[str, object]]
    columns: tuple[str, ...]


def _passing_conformance_result() -> RichResult:
    return RichResult(
        rows=[
            {
                "schema_ok": True,
                "missing_labels": [],
                "missing_relationship_types": [],
                "population": 2,
                "conforming_count": 2,
                "violation_count": 0,
                "coverage": 1.0,
                "evidence": [],
            }
        ],
        columns=(
            "schema_ok",
            "missing_labels",
            "missing_relationship_types",
            "population",
            "conforming_count",
            "violation_count",
            "coverage",
            "evidence",
        ),
    )


class RichClient:
    def __init__(self, responses, *, probe_result=TARGET):
        self.responses = list(responses)
        self.probe_result = probe_result
        self.probe_calls = 0
        self.read_calls = []

    def probe(self):
        self.probe_calls += 1
        return self.probe_result

    def run_read_result(self, query, params, *, timeout_s=None):
        self.read_calls.append((query, params, timeout_s))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def run_read(self, query, params):
        raise AssertionError("rich C2 path must be preferred over legacy run_read")


class LazyCompetencyClient:
    def __init__(self, rows, columns=("node_element_id",)):
        self.rows = rows
        self.columns = columns
        self.yielded = 0
        self.policies = []

    def run_read_result(self, query, params, *, timeout_s=None):
        raise AssertionError("competencies must use the bounded result path")

    def run_read_result_bounded(
        self,
        query,
        params,
        *,
        policy,
        timeout_s=None,
        stop_when=None,
    ):
        retained = []
        for row in self.rows:
            self.yielded += 1
            if policy.max_rows is not None and len(retained) >= policy.max_rows:
                raise GraphCheckError(
                    "engine.result_limit_exceeded",
                    "result limit exceeded",
                    "narrow the query",
                )
            retained.append(row)
            if stop_when is not None and stop_when(row):
                self.policies.append(policy)
                return QueryResult(
                    retained,
                    self.columns,
                    (),
                    complete=False,
                    observed_rows=self.yielded,
                    limit=policy.max_rows,
                )
        self.policies.append(policy)
        return QueryResult(
            retained,
            self.columns,
            (),
            complete=True,
            observed_rows=self.yielded,
            limit=policy.max_rows,
        )


class FixedClock:
    def __init__(self, *values):
        self.values = list(values)
        self.index = 0

    def __call__(self):
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class MonotonicSequence:
    def __init__(self, *values):
        self.values = list(values)
        self.index = 0

    def __call__(self):
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


def _engine(client, **kwargs):
    start = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    return Engine(
        client,
        clock=FixedClock(start, start + timedelta(seconds=1), start + timedelta(seconds=2)),
        monotonic=lambda: 10.0,
        id_factory=lambda: "run-fixed",
        **kwargs,
    )


def test_full_run_emits_frozen_results_shape_and_reproducibility_metadata():
    client = RichClient([_passing_conformance_result()], probe_result=(TARGET, object(), object()))
    start = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    engine = Engine(
        client,
        clock=FixedClock(start, start + timedelta(seconds=1), start + timedelta(seconds=2)),
        monotonic=lambda: 10.0,
        id_factory=lambda: "run-123",
    )

    results = engine.run_yaml(PASSING_SUITE, source="checks/customer.yml")
    payload = results.model_dump(mode="json", by_alias=True, exclude_none=False)

    assert set(payload) == {"schema_version", "run", "score", "totals", "suites", "checks"}
    assert results.schema_version == "2.0"
    assert results.run.id == "run-123"
    assert results.run.started_at == "2026-07-13T10:00:00Z"
    assert results.run.finished_at == "2026-07-13T10:00:02Z"
    assert results.run.graphcheck_version == __version__
    assert results.run.pack_version == PACK_VERSION
    assert results.run.run_status is RunStatus.COMPLETE
    assert results.run.target is not None
    assert results.run.target.model_dump(exclude={"nodes", "relationships"}) == TARGET.model_dump(
        exclude={"nodes", "relationships"}
    )
    assert results.run.selection.suites == ["customer-quality"]
    assert results.run.selection.fail_fast is False
    assert results.run.redaction.policy.value == "none"
    assert results.run.exit_code == 0
    assert results.suites[0].source_sha == hashlib.sha256(PASSING_SUITE.encode()).hexdigest()
    assert results.suites[0].score == 100
    assert results.totals.model_dump(by_alias=True)["pass"] == 1
    assert results.score is not None and results.score.value == 100
    assert len(results.checks) == 1
    check = results.checks[0]
    assert check.verdict is Verdict.PASS
    assert check.started_at == "2026-07-13T10:00:01Z"
    assert check.compiled_query and "(n:`Customer`)" in check.compiled_query
    assert check.params["required_labels"] == ["Customer"]
    assert "label" not in check.params
    assert check.measured == {
        "coverage": 1.0,
        "population": 2,
        "conforming": 2,
        "violations": 0,
    }
    assert client.probe_calls == 1
    assert len(client.read_calls) == 1
    assert client.read_calls[0][2] > 0


def test_supplied_target_without_inventory_fails_before_checks():
    target = RunTarget(
        database="neo4j",
        server_version="5.18.0",
        edition="community",
        fingerprint="sha256:legacy-target",
        capabilities=Capabilities(apoc=False, count_store=True),
    )

    results = _engine(RichClient([_passing_conformance_result()])).run_yaml(
        PASSING_SUITE, target=target
    )

    assert results.run.run_status is RunStatus.FAILED
    assert results.run.error is not None
    assert results.run.error.code == "engine.target_inventory_missing"


def test_failing_conformance_collects_bounded_evidence_in_one_read_transaction():
    measurement = RichResult(
        [
            {
                "schema_ok": True,
                "missing_labels": [],
                "missing_relationship_types": [],
                "population": 2,
                "conforming_count": 1,
                "violation_count": 1,
                "coverage": 0.5,
                "evidence": [],
            }
        ],
        ("population", "conforming_count", "violation_count", "coverage", "evidence"),
    )
    evidence = RichResult(
        [{"evidence": [{"kind": "node", "id": "7", "labels": ["Customer"]}]}],
        ("evidence",),
    )

    class TransactionClient(RichClient):
        def __init__(self):
            super().__init__([measurement, evidence])
            self.transactions = 0

        @contextmanager
        def read_transaction(self, *, timeout_s=None):
            self.transactions += 1
            yield self

    client = TransactionClient()
    results = _engine(client).run_yaml(PASSING_SUITE, target=TARGET)

    assert results.checks[0].verdict is Verdict.FAIL
    assert results.checks[0].measured["violations"] == 1
    assert results.checks[0].evidence.elements[0].id == "7"
    assert client.transactions == 1
    assert len(client.read_calls) == 2
    assert client.read_calls[1][1] == {"evidence_cap": 100}
    assert client.read_calls[1][2] <= client.read_calls[0][2]


def test_evidence_query_failure_is_an_errored_check():
    client = RichClient(
        [
            RichResult(
                [
                    {
                        "schema_ok": True,
                        "missing_labels": [],
                        "missing_relationship_types": [],
                        "population": 1,
                        "conforming_count": 0,
                        "violation_count": 1,
                        "coverage": 0.0,
                        "evidence": [],
                    }
                ],
                ("population", "conforming_count", "violation_count", "coverage", "evidence"),
            ),
            GraphCheckError("neo4j.query_failed", "evidence failed", "fix query"),
        ]
    )

    check = _engine(client).run_yaml(PASSING_SUITE, target=TARGET).checks[0]

    assert check.verdict is Verdict.ERRORED
    assert check.error.code == "neo4j.query_failed"
    assert check.measured is None
    assert check.evidence is None


def test_tag_selection_filters_the_universe_and_records_metadata():
    suite = """\
suite: tagged
competency:
  - id: production-check
    tags: [production]
    question: Does production return a value?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
  - id: development-check
    tags: [development]
    question: Does development return a value?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
    client = RichClient([QueryResult([{"value": 1}], ("value",), ())])

    results = _engine(client).run_yaml(suite, target=TARGET, tags=["production"])

    assert [check.id for check in results.checks] == ["production-check"]
    assert results.run.selection.tags == ["production"]
    assert results.run.exit_code == 0
    assert len(client.read_calls) == 1


def test_fail_fast_marks_remaining_selected_checks_not_run_and_partial():
    suite = """\
suite: fail-fast
competency:
  - id: first
    question: Is this result empty?
    query: RETURN 1 AS value
    expect: {empty: true}
  - id: second
    question: Would this check run normally?
    query: RETURN 2 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
    client = RichClient(
        [
            QueryResult(
                [
                    {
                        "value": 1,
                        "evidence": {
                            "kind": "node",
                            "id": "node-1",
                            "labels": ["Customer"],
                        },
                    }
                ],
                ("value", "evidence"),
                (),
            )
        ]
    )

    results = _engine(client).run_yaml(suite, target=TARGET, fail_fast=True)

    assert [check.verdict for check in results.checks] == [Verdict.FAIL, Verdict.SKIPPED]
    assert results.checks[1].skip_reason is SkipReason.NOT_RUN
    assert results.run.run_status is RunStatus.PARTIAL
    assert "fail-fast stopped the run after fail-fast/first" in results.run.partial_reason
    assert results.run.selection.fail_fast is True
    assert results.run.exit_code == 1
    assert len(client.read_calls) == 1


def test_run_deadline_is_propagated_to_timeout_aware_target_probe():
    captured = {}

    class TimedProbeClient(RichClient):
        def probe(self, *, timeout_s):
            captured["timeout_s"] = timeout_s
            return TARGET

    client = TimedProbeClient([_passing_conformance_result()])

    results = _engine(client).run_yaml(PASSING_SUITE)

    assert results.checks[0].verdict is Verdict.PASS
    assert captured["timeout_s"] == pytest.approx(295.0)


def test_probe_that_returns_after_deadline_fails_the_run_before_checks():
    class SlowLegacyProbeClient:
        def probe(self):
            return TARGET

    engine = Engine(
        SlowLegacyProbeClient(),
        config=EngineConfig(time_budget_s=1.0),
        clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)),
        monotonic=MonotonicSequence(0.0, 0.0, 1.1),
        id_factory=lambda: "run-slow-probe",
    )

    results = engine.run_yaml(PASSING_SUITE)

    assert results.run.run_status is RunStatus.FAILED
    assert results.run.error.code == "engine.timeout"
    assert results.checks == []


def test_suite_input_sha_hashes_exact_yaml_bytes():
    text = PASSING_SUITE.replace("\n", "\r\n")

    suite_input = SuiteInput.from_yaml(text, source="checks/customer.yml")

    assert suite_input.source_sha == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_unloadable_suite_does_not_discard_other_suite_results():
    client = RichClient([RichResult([{"value": 1}], ("value",))])
    invalid = "suite: broken\nunknown_top_level: true\n"

    results = _engine(client).run_yamls(
        [
            YamlSuiteInput(invalid, source="checks/broken.yml"),
            YamlSuiteInput(ONE_COMPETENCY, source="checks/healthy.yml"),
        ],
        target=TARGET,
    )

    assert results.run.run_status is RunStatus.PARTIAL
    assert "checks/broken.yml could not be loaded" in results.run.partial_reason
    assert results.run.selection.suites == ["competencies"]
    assert [suite.id for suite in results.suites] == ["competencies"]
    assert results.checks[0].verdict is Verdict.PASS


def test_generated_check_is_validated_then_skipped_without_connector_read():
    client = RichClient([])

    results = _engine(client).run_yaml(GENERATED_SUITE, target=TARGET)

    assert results.run.run_status is RunStatus.COMPLETE
    assert results.run.exit_code == 2
    assert results.score is None
    assert len(results.checks) == 1
    check = results.checks[0]
    assert check.verdict is Verdict.SKIPPED
    assert check.skip_reason is SkipReason.GENERATED
    assert check.compiled_query is None
    assert check.params is None
    assert client.probe_calls == 0
    assert client.read_calls == []


def test_unobservable_dangling_check_is_explicit_unsupported_partial_skip():
    client = RichClient([])

    results = _engine(client).run_yaml(DANGLING_SUITE, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.SKIPPED
    assert check.skip_reason is SkipReason.UNSUPPORTED
    assert results.run.run_status is RunStatus.PARTIAL
    assert "requires missing capability: store_consistency" in results.run.partial_reason
    assert client.read_calls == []


def test_one_query_error_is_isolated_and_later_check_still_passes():
    failure = GraphCheckError("neo4j.query_failed", "broken query", "fix query")
    client = RichClient([failure, RichResult([{"value": 1}], ("value",))])

    results = _engine(client).run_yaml(TWO_COMPETENCIES, target=TARGET)

    assert [check.verdict for check in results.checks] == [Verdict.ERRORED, Verdict.PASS]
    assert results.checks[0].error.code == "neo4j.query_failed"
    assert results.checks[0].measured is None
    assert results.checks[0].evidence is None
    assert results.checks[1].measured["rows"] == 1
    assert len(client.read_calls) == 2
    assert results.run.run_status is RunStatus.COMPLETE
    assert results.totals.errored == 1


def test_non_fail_fast_checks_use_bounded_concurrency():
    barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    class ConcurrentClient:
        def run_read_result(self, query, params, *, timeout_s=None):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                barrier.wait(timeout=2)
                return RichResult([{"value": 1}], ("value",))
            finally:
                with lock:
                    active -= 1

    results = _engine(ConcurrentClient(), config=EngineConfig(max_concurrency=2)).run_yaml(
        TWO_COMPETENCIES, target=TARGET
    )

    assert [check.verdict for check in results.checks] == [Verdict.PASS, Verdict.PASS]
    assert maximum_active == 2


def test_compile_error_is_errored_and_never_passed_or_queried():
    class BrokenCompiler:
        def compile(self, check, *, sample_seed=0):
            raise GraphCheckError("engine.compile_broken", "cannot compile", "fix check")

    client = RichClient([])
    results = _engine(client, compiler=BrokenCompiler()).run_yaml(TWO_COMPETENCIES, target=TARGET)

    assert all(check.verdict is Verdict.ERRORED for check in results.checks)
    assert all(check.error.code == "engine.compile_broken" for check in results.checks)
    assert all(check.compiled_query is None for check in results.checks)
    assert all(check.measured is None for check in results.checks)
    assert client.read_calls == []


def test_evaluator_error_is_errored_and_never_passed():
    class BrokenEvaluator:
        def evaluate(self, compiled, rows, *, columns=None, baseline=None):
            raise GraphCheckError("engine.evaluate_broken", "cannot evaluate", "fix evaluator")

    client = RichClient([RichResult([{"value": 1}], ("value",))])

    results = _engine(client, evaluator=BrokenEvaluator()).run_yaml(ONE_COMPETENCY, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.ERRORED
    assert check.error.code == "engine.evaluate_broken"
    assert check.compiled_query is not None
    assert check.params == {}
    assert check.measured is None
    assert check.evidence is None


def test_resolved_parameter_value_is_used_for_failure_evidence():
    class Resolver:
        def resolve(self, token, client, *, timeout_s):
            assert token == "$dynamic-node"
            return "4:graph:resolved"

    suite = """\
suite: resolved-evidence
competency:
  - id: must-return
    question: Does the selected node produce a row?
    query: RETURN $node_element_id AS node_element_id
    params: {node_element_id: "$dynamic-node"}
    expect: {empty: false}
"""
    client = RichClient([RichResult([], ("node_element_id",))])

    results = _engine(client, parameter_resolver=Resolver()).run_yaml(suite, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.FAIL
    assert check.params == {"node_element_id": "4:graph:resolved"}
    assert check.evidence.elements[0].id == "4:graph:resolved"


def test_in_flight_budget_timeout_is_partial_exit_two_and_stops_later_work():
    timeout = GraphCheckTimeoutError("neo4j.query_failed", "query timed out", "narrow query")
    client = RichClient([timeout])
    start = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    engine = Engine(
        client,
        config=EngineConfig(time_budget_s=1.0),
        clock=FixedClock(start, start + timedelta(seconds=1), start + timedelta(seconds=2)),
        monotonic=MonotonicSequence(0.0, 0.0, 0.0, 0.0, 0.0, 1.1, 1.1),
        id_factory=lambda: "run-timeout",
    )

    results = engine.run_yaml(TWO_COMPETENCIES, target=TARGET, fail_fast=True)

    assert results.run.run_status is RunStatus.PARTIAL
    assert results.run.partial_reason and "budget was exhausted" in results.run.partial_reason
    assert "fail-fast stopped" not in results.run.partial_reason
    assert results.run.exit_code == 2
    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "engine.timeout"
    assert results.checks[1].verdict is Verdict.SKIPPED
    assert results.checks[1].skip_reason is SkipReason.NOT_RUN
    assert len(client.read_calls) == 1


def test_server_query_timeout_before_deadline_remains_a_hard_fail_fast_error():
    timeout = GraphCheckTimeoutError("neo4j.query_failed", "query timed out", "narrow query")
    client = RichClient([timeout])
    engine = Engine(
        client,
        config=EngineConfig(time_budget_s=10.0),
        clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)),
        monotonic=lambda: 0.0,
        id_factory=lambda: "run-query-timeout",
    )

    results = engine.run_yaml(TWO_COMPETENCIES, target=TARGET, fail_fast=True)

    assert results.run.run_status is RunStatus.PARTIAL
    assert results.run.partial_reason == "fail-fast stopped the run after competencies/first"
    assert results.run.exit_code == 1
    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "neo4j.query_failed"
    assert results.checks[1].verdict is Verdict.SKIPPED
    assert results.checks[1].skip_reason is SkipReason.NOT_RUN
    assert len(client.read_calls) == 1


def test_empty_target_drift_with_missing_schema_is_errored_not_passed():
    client = RichClient(
        [
            RichResult(
                [
                    {
                        "schema_ok": False,
                        "missing_labels": ["Customer"],
                        "missing_relationship_types": [],
                        "current": 100,
                        "population": 0,
                        "evidence": [],
                    }
                ],
                ("schema_ok", "current", "population", "evidence"),
            )
        ]
    )

    results = _engine(
        client,
        baselines={"latest": {"node_count": {"label=Customer": 100}}},
    ).run_yaml(DRIFT_SUITE, target=EMPTY_TARGET)

    assert results.run.exit_code == 1
    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "engine.schema_reference_missing"


def test_last_check_finishing_after_deadline_marks_the_run_partial():
    client = RichClient([RichResult([{"value": 1}], ("value",))])
    engine = Engine(
        client,
        config=EngineConfig(time_budget_s=1.0),
        clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)),
        monotonic=MonotonicSequence(0.0, 0.0, 0.0, 0.0, 0.0, 1.1, 1.1),
        id_factory=lambda: "run-over-budget",
    )

    results = engine.run_yaml(ONE_COMPETENCY, target=TARGET)

    assert results.checks[0].verdict is Verdict.PASS
    assert results.run.run_status is RunStatus.PARTIAL
    assert "budget was exhausted" in results.run.partial_reason


def test_missing_drift_baseline_is_errored_before_query_execution():
    client = RichClient([])

    results = _engine(client).run_yaml(DRIFT_SUITE, target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.ERRORED
    assert check.error.code == "engine.baseline_missing"
    assert check.compiled_query is not None
    assert check.params is not None
    assert check.measured is None
    assert client.read_calls == []


def test_present_measurement_from_partial_drift_baseline_can_complete():
    client = RichClient(
        [
            RichResult(
                [
                    {
                        "schema_ok": True,
                        "missing_labels": [],
                        "missing_relationship_types": [],
                        "current": 100,
                        "population": 100,
                        "evidence": [],
                    }
                ],
                (
                    "schema_ok",
                    "missing_labels",
                    "missing_relationship_types",
                    "current",
                    "population",
                    "evidence",
                ),
            )
        ]
    )
    baselines = {
        "latest": {
            "status": "partial",
            "node_count": {"label=Customer": 100},
        }
    }

    results = _engine(client, baselines=baselines).run_yaml(DRIFT_SUITE, target=TARGET)

    assert results.run.run_status is RunStatus.COMPLETE
    assert results.run.partial_reason is None
    assert results.run.exit_code == 0
    assert results.checks[0].verdict is Verdict.PASS
    assert results.checks[0].measured["baseline"] == 100.0
    assert results.checks[0].measured["current"] == 100.0


@pytest.mark.parametrize(
    (
        "metric",
        "target",
        "baseline_key",
        "current",
        "severity",
        "expected_verdict",
        "expected_id",
    ),
    [
        pytest.param(
            "node_count",
            "{label: Customer}",
            "label=Customer",
            80,
            "error",
            Verdict.FAIL,
            "node_count:label=Customer",
            id="node-count-decrease",
        ),
        pytest.param(
            "node_count",
            "{label: Customer}",
            "label=Customer",
            0,
            "error",
            Verdict.FAIL,
            "node_count:label=Customer",
            id="node-count-decrease-to-zero",
        ),
        pytest.param(
            "relationship_count",
            "{type: OWNS}",
            "type=OWNS",
            80,
            "warn",
            Verdict.WARN,
            "relationship_count:type=OWNS",
            id="relationship-count-decrease",
        ),
    ],
)
def test_compiled_count_drift_failures_use_aggregate_scope_evidence(
    metric,
    target,
    baseline_key,
    current,
    severity,
    expected_verdict,
    expected_id,
):
    suite = f"""\
suite: aggregate-drift
drift:
  - id: changed-count
    severity: {severity}
    metric: {metric}
    target: {target}
    baseline: latest
    tolerance: {{max_drop_pct: 10}}
"""
    client = RichClient(
        [
            QueryResult(
                [
                    {
                        "schema_ok": True,
                        "missing_labels": [],
                        "missing_relationship_types": [],
                        "current": current,
                        "population": current,
                        "evidence": [],
                    }
                ],
                (
                    "schema_ok",
                    "missing_labels",
                    "missing_relationship_types",
                    "current",
                    "population",
                    "evidence",
                ),
                (),
            )
        ]
    )
    baselines = {"latest": {metric: {baseline_key: 100}}}

    results = _engine(client, baselines=baselines).run_yaml(suite, target=TARGET)

    check = results.checks[0]
    assert check.verdict is expected_verdict
    assert check.error is None
    assert check.measured == {
        "current": float(current),
        "baseline": 100.0,
        "delta": float(current - 100),
        "change_pct": float(current - 100),
    }
    assert "[] AS evidence" in check.compiled_query
    assert check.evidence is not None
    assert check.evidence.elements[0].kind == "aggregate"
    assert check.evidence.elements[0].id == expected_id
    assert check.evidence.total_count == 1
    assert check.evidence.truncated is False
    query, params, _timeout = client.read_calls[0]
    assert query == check.compiled_query
    assert params == check.params


def test_partial_baseline_missing_measurement_keeps_the_run_partial():
    suite = """\
suite: partial-missing
drift:
  - id: coverage
    metric: property_coverage
    target: {label: Customer, property: tax_id}
    baseline: latest
    tolerance: {max_drop_pct: 5}
"""
    client = RichClient([])
    baselines = {
        "latest": {
            "status": "partial",
            "statistics": {"node_count": 10, "relationship_count": 0},
            "schema": {"labels": [], "relationship_types": []},
        }
    }

    results = _engine(client, baselines=baselines).run_yaml(suite, target=TARGET)

    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "engine.baseline_partial_missing"
    assert results.run.run_status is RunStatus.PARTIAL
    assert "partial baseline" in results.run.partial_reason
    assert client.read_calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            GraphCheckError("neo4j.unreachable", "cannot connect", "start database"),
            "neo4j.unreachable",
        ),
        (RuntimeError("probe exploded"), "engine.internal_error"),
    ],
)
def test_target_probe_failure_returns_failed_run_without_check_results(failure, expected_code):
    class ProbeFailureClient:
        def probe(self):
            raise failure

    results = _engine(ProbeFailureClient()).run_yaml(PASSING_SUITE)

    assert results.run.run_status is RunStatus.FAILED
    assert results.run.exit_code == 3
    assert results.run.target is None
    assert results.run.error.code == expected_code
    assert results.run.selection.suites == ["customer-quality"]
    assert results.score is None
    assert results.suites == []
    assert results.checks == []
    assert results.totals.checks == 0


def test_legacy_c2_run_read_api_executes_without_timeout_keyword():
    class LegacyClient:
        def __init__(self):
            self.calls = []

        def run_read(self, query, params):
            self.calls.append((query, params))
            return [{"value": 1}]

    client = LegacyClient()

    results = _engine(client).run_yaml(ONE_COMPETENCY, target=TARGET)

    assert results.checks[0].verdict is Verdict.PASS
    assert len(client.calls) == 1


def test_connector_without_read_api_produces_errored_check_not_pass():
    results = _engine(object()).run_yaml(TWO_COMPETENCIES, target=TARGET)

    assert all(check.verdict is Verdict.ERRORED for check in results.checks)
    assert all(check.error.code == "engine.connector_invalid" for check in results.checks)


def test_duplicate_suite_ids_fail_before_target_or_check_execution():
    first = SuiteInput.from_yaml(ONE_COMPETENCY)
    second = SuiteInput.from_yaml(ONE_COMPETENCY)
    client = RichClient([])

    results = _engine(client).run([first, second], target=TARGET)

    assert results.run.run_status is RunStatus.FAILED
    assert results.run.error.code == "engine.duplicate_suite"
    assert results.checks == []
    assert client.probe_calls == 0
    assert client.read_calls == []


def test_invalid_injected_evaluator_result_is_errored_not_truth_tested():
    class InvalidEvaluator:
        def evaluate(self, compiled, rows, *, columns=None, baseline=None):
            return object()

    client = RichClient([RichResult([{"value": 1}], ("value",))])
    results = _engine(client, evaluator=InvalidEvaluator()).run_yaml(ONE_COMPETENCY, target=TARGET)

    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "engine.invalid_evaluation"


@pytest.mark.parametrize("invalid", [0, -1, True, float("nan"), "5"])
def test_engine_config_rejects_invalid_time_budgets(invalid):
    with pytest.raises(ValueError, match="finite and positive"):
        EngineConfig(time_budget_s=invalid)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10"])
def test_engine_config_rejects_non_positive_integer_evidence_caps(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        EngineConfig(evidence_cap=invalid)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "4"])
def test_engine_config_rejects_invalid_max_concurrency(invalid):
    with pytest.raises(ValueError, match="max_concurrency"):
        EngineConfig(max_concurrency=invalid)


def test_engine_config_defaults_to_two_workers():
    assert EngineConfig().max_concurrency == 2


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "100"])
def test_engine_config_rejects_invalid_result_row_limits(invalid):
    with pytest.raises(ValueError, match="result_row_limit"):
        EngineConfig(result_row_limit=invalid)


def test_competency_minimum_stops_at_the_first_decisive_row():
    client = LazyCompetencyClient({"node_element_id": f"n-{index}"} for index in range(1_000_000))
    suite = """\
suite: bounded
competency:
  - id: minimum
    question: Are at least three nodes returned?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {rows: {min: 3}}
"""

    result = _engine(client).run_yaml(suite, target=TARGET).checks[0]

    assert result.verdict is Verdict.PASS
    assert client.yielded == 3
    assert result.measured["observed_rows_at_least"] == 3
    assert "rows" not in result.measured


def test_competency_maximum_and_duplicate_fail_at_the_first_decisive_row():
    maximum = LazyCompetencyClient({"node_element_id": f"n-{index}"} for index in range(100))
    duplicate = LazyCompetencyClient(
        iter(
            [
                {"node_element_id": "n-1"},
                {"node_element_id": "n-1"},
                {"node_element_id": "never-read"},
            ]
        )
    )
    maximum_suite = """\
suite: bounded-max
competency:
  - id: maximum
    question: Are at most two nodes returned?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect:
      rows: {max: 2}
      unique: false
      contains: [never-read]
      equals: [never-read]
"""
    duplicate_suite = """\
suite: bounded-unique
competency:
  - id: unique
    question: Are rows unique?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {unique: true}
"""

    maximum_result = _engine(maximum).run_yaml(maximum_suite, target=TARGET).checks[0]
    duplicate_result = _engine(duplicate).run_yaml(duplicate_suite, target=TARGET).checks[0]

    assert maximum_result.verdict is Verdict.FAIL
    assert duplicate_result.verdict is Verdict.FAIL
    assert maximum.yielded == 3
    assert duplicate.yielded == 2
    assert "rows" not in maximum_result.measured
    assert {"unique", "contains", "equals"}.isdisjoint(maximum_result.measured)
    assert duplicate_result.measured["unique"] is False


def test_combined_expectations_use_the_strictest_consumption_policy():
    client = LazyCompetencyClient(
        iter(
            [
                {"node_element_id": "n-1"},
                {"node_element_id": "n-2"},
                {"node_element_id": "n-3"},
            ]
        )
    )
    suite = """\
suite: strictest
competency:
  - id: combined
    question: Are there enough unique rows?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {rows: {min: 2}, unique: true}
"""

    result = _engine(client).run_yaml(suite, target=TARGET).checks[0]

    assert result.verdict is Verdict.PASS
    assert client.yielded == 3
    assert result.measured["rows"] == 3


def test_contains_stops_when_every_pinned_value_has_been_seen():
    client = LazyCompetencyClient({"node_element_id": f"n-{index}"} for index in range(100))
    suite = """\
suite: bounded-contains
competency:
  - id: contains
    question: Does the result contain both pinned ids?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {contains: [n-1, n-3]}
"""

    result = _engine(client).run_yaml(suite, target=TARGET).checks[0]

    assert result.verdict is Verdict.PASS
    assert result.measured["contains"] is True
    assert client.yielded == 4
    assert "rows" not in result.measured


def test_complete_assertion_errors_loudly_at_the_safety_ceiling():
    client = LazyCompetencyClient({"node_element_id": f"n-{index}"} for index in range(100))
    suite = """\
suite: bounded-equals
competency:
  - id: equals
    question: Does the full result equal the pinned bag?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {equals: [n-1]}
"""

    result = (
        _engine(
            client,
            config=EngineConfig(result_row_limit=2),
        )
        .run_yaml(suite, target=TARGET)
        .checks[0]
    )

    assert result.verdict is Verdict.ERRORED
    assert result.error.code == "engine.result_limit_exceeded"
    assert client.yielded == 3
