import json
from contextlib import contextmanager

import pytest
import yaml

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import COMPLETENESS_BATCH_SIZE, CypherCompiler
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.telemetry.events import CheckProcessed, QueryFinished, QueryRole
from tests.unit.engine.test_runner import EMPTY_TARGET, TARGET


def suite(properties=("a", "b", "a"), thresholds=(1.0, 0.5, 0.5), label="Customer"):
    return load_suite(
        yaml.safe_dump(
            {
                "suite": "batch",
                "conformance": [
                    {
                        "id": f"c{i}",
                        "check": "completeness",
                        "with": {
                            "label": label,
                            "property": prop,
                            "threshold": thresholds[i % len(thresholds)],
                        },
                    }
                    for i, prop in enumerate(properties)
                ],
            }
        )
    )


class Client:
    def __init__(self, *, population=2, schema=True, failure=False):
        self.population, self.schema, self.failure = population, schema, failure
        self.queries = []
        self.transactions = 0

    @contextmanager
    def read_transaction(self, **kwargs):
        self.transactions += 1
        yield self

    def run_read(self, query, params, **kwargs):
        self.queries.append(query)
        if query.startswith("// completeness outputs:") and self.failure:
            raise GraphCheckError("neo4j.query_failed", "batch failed", "fix")
        if query.startswith("MATCH ") and "AS evidence" in query:
            return [{"evidence": [{"kind": "node", "id": "n1", "labels": ["Customer"]}]}]
        conforming = self.population // 2
        row = {
            "schema_ok": self.schema,
            "missing_labels": [] if self.schema else ["Customer"],
            "missing_relationship_types": [],
            "population": self.population,
            "conforming_count": conforming,
            "violation_count": self.population - conforming,
            "coverage": conforming / self.population if self.population else 1.0,
            "evidence": [],
        }
        if query.startswith("// completeness outputs:"):
            columns = json.loads(query.splitlines()[0].partition(": ")[2]).values()
            row.update({column: conforming for column in columns})
        return [row]


def test_batch_compiler_deduplicates_counters_and_retains_evidence():
    batch = CypherCompiler().compile_completeness_batch(suite(label="Cu`stomer").checks)
    assert batch.query.count("MATCH ") == 1
    assert "`Cu``stomer`" in batch.query
    assert batch.query.count("count(n.`a`)") == 1
    assert [column for _, column in batch.members] == [
        "conforming_0",
        "conforming_1",
        "conforming_0",
    ]
    assert all(member.evidence_query for member, _ in batch.members)
    assert all(
        member.query == batch.query and member.params == batch.params for member, _ in batch.members
    )
    with pytest.raises(ValueError):
        CypherCompiler().compile_completeness_batch(suite(properties=["a"] * 33).checks)


@pytest.mark.parametrize(
    "population,schema,target", [(2, True, TARGET), (0, False, EMPTY_TARGET), (0, False, TARGET)]
)
@pytest.mark.parametrize("concurrency", [1, 4])
def test_grouped_results_match_individual_measurements(population, schema, target, concurrency):
    grouped_client = Client(population=population, schema=schema)
    individual_client = Client(population=population, schema=schema)
    checks = suite()
    progress = []
    grouped = Engine(
        grouped_client,
        config=EngineConfig(max_concurrency=concurrency),
        progress_callback=lambda *args: progress.append(args),
    ).run_suite(checks, target=target)
    individual = [
        Engine(individual_client)
        .run_suite(checks.model_copy(update={"checks": [check]}), target=target)
        .checks[0]
        for check in checks.checks
    ]
    for actual, expected in zip(grouped.checks, individual, strict=True):
        assert (actual.verdict, actual.measured, actual.evidence, actual.error) == (
            expected.verdict,
            expected.measured,
            expected.evidence,
            expected.error,
        )
        assert actual.compiled_query.startswith("// completeness outputs:")
    assert len(progress) == 3 and progress[-1][:2] == (3, 3)
    assert len(grouped_client.queries) < len(individual_client.queries)


def test_failed_batch_fans_out_once_and_unrelated_group_continues():
    client = Client(failure=True)
    checks = suite()
    checks.checks[-1].spec.with_["label"] = "Other"
    result = Engine(client).run_suite(checks, target=TARGET)
    assert [check.verdict.value for check in result.checks] == ["errored", "errored", "pass"]
    assert len(client.queries) == 2


def test_shared_measurement_is_counted_once_in_telemetry():
    events = []

    class Sink:
        def emit(self, event):
            events.append(event)

    result = Engine(Client(), event_sink=Sink()).run_suite(suite(), target=TARGET)
    assert result.run.exit_code == 1
    queries = [event for event in events if isinstance(event, QueryFinished)]
    assert sum(event.query_role is QueryRole.CHECK_MEASUREMENT for event in queries) == 1
    assert sum(event.query_count for event in events if isinstance(event, CheckProcessed)) == 2


def test_group_width_is_bounded_and_fail_fast_uses_individual_path():
    client = Client()
    result = Engine(client).run_suite(
        suite(properties=["a"] * (COMPLETENESS_BATCH_SIZE + 2), thresholds=[0.5]), target=TARGET
    )
    assert len(result.checks) == 34 and len(client.queries) == 2
    client = Client()
    result = Engine(client).run_suite(suite(), target=TARGET, fail_fast=True)
    assert result.checks[1].skip_reason.value == "not_run"
    assert all(not query.startswith("// completeness outputs:") for query in client.queries)


def test_batch_timeout_fans_out_without_retrying_and_exhausts_shared_deadline():
    now = [0.0]

    class TimeoutClient(Client):
        def run_read(self, query, params, **kwargs):
            self.queries.append(query)
            now[0] = 2.0
            raise GraphCheckTimeoutError("engine.timeout", "timed out", "reduce the work")

    client = TimeoutClient()
    checks = suite()
    checks.checks[-1].spec.with_["label"] = "Other"
    result = Engine(
        client, monotonic=lambda: now[0], config=EngineConfig(max_concurrency=1, time_budget_s=1)
    ).run_suite(checks, target=TARGET)
    assert result.run.run_status.value == "partial"
    assert [check.error.code for check in result.checks[:2]] == ["engine.timeout"] * 2
    assert result.checks[-1].skip_reason.value == "not_run"
    assert len(client.queries) == 1


def test_batch_compile_failure_does_not_abort_unrelated_checks():
    checks = suite()
    checks.checks[0].spec.with_["threshold"] = 2
    checks.checks[-1].spec.with_["label"] = "Other"
    result = Engine(Client()).run_suite(checks, target=TARGET)
    assert [check.verdict.value for check in result.checks] == ["errored", "errored", "pass"]


def test_generated_checks_are_filtered_before_grouping():
    checks = suite()
    checks.checks[0].generated = True
    client = Client()
    result = Engine(client).run_suite(checks, target=TARGET)
    assert result.checks[0].skip_reason.value == "generated"
    assert [check.id for check in result.checks] == ["c0", "c1", "c2"]
    assert len(client.queries) == 1
    assert '"c0"' not in client.queries[0]
