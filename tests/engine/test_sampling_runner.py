from dataclasses import dataclass

import pytest

from graphcheck.contracts.check import ConformanceCheck, LoadedCheck, Suite
from graphcheck.contracts.results import Capabilities, Pattern, ResultsTarget, Severity, Verdict
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.engine.sampling import SamplingPolicy

TARGET = ResultsTarget(
    database="neo4j",
    server_version="5",
    edition="community",
    fingerprint="sha256:sample-graph",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)


@dataclass(frozen=True)
class RichResult:
    rows: list[dict[str, object]]
    columns: tuple[str, ...]


class Client:
    def __init__(self, population: int, sample_size: int, violations: int):
        self.population = population
        self.sample_size = sample_size
        self.violations = violations
        self.calls = []

    def run_read_result(self, query, params, *, timeout_s=None):
        self.calls.append((query, dict(params), timeout_s))
        return RichResult(
            [
                {
                    "schema_ok": True,
                    "missing_labels": [],
                    "missing_relationship_types": [],
                    "population": self.population,
                    "sample_size": self.sample_size,
                    "violation_count": self.violations,
                    "mean_degree": 3.0,
                    "degree_stddev": 1.0,
                    "evidence": (
                        [{"kind": "node", "id": "hub-1", "labels": ["Customer"]}]
                        if self.violations
                        else []
                    ),
                }
            ],
            ("population", "sample_size", "violation_count", "evidence"),
        )


def _suite(*, sample_size=None) -> Suite:
    config = {
        "label": "Customer",
        "rel_type": "CONTROLS",
        "direction": "any",
        "z_threshold": 3.0,
        "sample_size": sample_size,
    }
    spec = ConformanceCheck.model_validate({"id": "hubs", "check": "hub_outlier", "with": config})
    check = LoadedCheck(
        id="hubs",
        pattern=Pattern.CONFORMANCE,
        severity=Severity.ERROR,
        tags=[],
        provenance=None,
        generated=False,
        spec=spec,
    )
    return Suite(suite="sampling", checks=[check])


def _engine(client, *, exhaustive_limit=10, sample_size=3):
    config = EngineConfig(
        sampling=SamplingPolicy(
            exhaustive_limit=exhaustive_limit,
            sample_size=sample_size,
            seed="pinned-seed",
        )
    )
    return Engine(client, config=config, monotonic=lambda: 1.0)


def test_large_population_uses_policy_sample_and_emits_estimate_metadata():
    client = Client(population=100, sample_size=3, violations=1)

    results = _engine(client).run_suite(_suite(), source_sha="suite-sha", target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.FAIL
    assert check.estimate is not False
    assert check.estimate.sample_size == 3
    assert check.estimate.population == 100
    assert check.estimate.confidence == pytest.approx(0.95)
    assert check.params["sample_size"] == 3
    assert check.expected["sample_size"] == 3
    for name in ("sample_hash_a", "sample_hash_b", "sample_hash_c", "sample_hash_d"):
        assert isinstance(check.params[name], int)
    assert len(client.calls) == 1
    assert client.calls[0][1]["sample_size"] == 3
    assert "(n:`Customer`)" in client.calls[0][0]
    assert "_gc_gate_key" in client.calls[0][0]


def test_population_inside_exhaustive_limit_is_exact_and_not_labeled_estimate():
    client = Client(population=5, sample_size=5, violations=0)

    results = _engine(client).run_suite(_suite(), source_sha="suite-sha", target=TARGET)

    check = results.checks[0]
    assert check.verdict is Verdict.PASS
    assert check.estimate is False
    assert check.params["sample_size"] == 3
    assert check.params["exhaustive_limit"] == 10
    assert check.expected["sample_size"] == 3


def test_check_level_sample_size_overrides_global_exhaustive_decision():
    client = Client(population=5, sample_size=2, violations=1)

    results = _engine(client).run_suite(
        _suite(sample_size=2), source_sha="suite-sha", target=TARGET
    )

    check = results.checks[0]
    assert check.estimate is not False
    assert check.estimate.sample_size == 2
    assert check.params["sample_size"] == 2
    assert check.params["exhaustive_limit"] == 2


def test_check_level_sample_size_cannot_exceed_global_policy_cap():
    client = Client(population=100, sample_size=3, violations=0)

    results = _engine(client, sample_size=3).run_suite(
        _suite(sample_size=50), source_sha="suite-sha", target=TARGET
    )

    check = results.checks[0]
    assert check.verdict is Verdict.PASS
    assert check.estimate is not False
    assert check.estimate.sample_size == 3
    assert check.params["sample_size"] == 3
    assert check.expected["sample_size"] == 3
    assert client.calls[0][1]["sample_size"] == 3


def test_pack_default_sample_size_reduces_a_larger_global_policy_decision():
    client = Client(population=200_000, sample_size=1000, violations=0)

    results = _engine(client, exhaustive_limit=100_000, sample_size=10_000).run_suite(
        _suite(), source_sha="suite-sha", target=TARGET
    )

    check = results.checks[0]
    assert check.verdict is Verdict.PASS
    assert check.estimate is not False
    assert check.estimate.sample_size == 1000
    assert check.params["sample_size"] == 1000
    assert client.calls[0][1]["sample_size"] == 1000


def test_same_graph_suite_and_seed_produce_the_same_query_hash():
    first_client = Client(population=100, sample_size=3, violations=0)
    second_client = Client(population=100, sample_size=3, violations=0)

    first = _engine(first_client).run_suite(_suite(), source_sha="suite-sha", target=TARGET)
    second = _engine(second_client).run_suite(_suite(), source_sha="suite-sha", target=TARGET)

    for name in ("sample_hash_a", "sample_hash_b", "sample_hash_c", "sample_hash_d"):
        assert first.checks[0].params[name] == second.checks[0].params[name]
