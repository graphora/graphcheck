from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from graphcheck.contracts.check import load_suite
from graphcheck.engine import Engine, EngineConfig
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.reporting import load_results
from tools import benchmark_agent_authoring as benchmark

ROOT = Path(__file__).parents[2]
SUITE = """suite: example
generated: true
competency:
  - id: example
    question: Does the fixture have the expected count?
    query: MATCH (c:Customer) RETURN count(c) AS count
    expect: {equals: [1500]}
"""


def test_activation_preserves_raw_proposal_and_changes_only_generated_flags():
    suite = load_suite(SUITE)
    before = suite.model_dump()
    active = benchmark.activated(suite)
    assert suite.model_dump() == before
    assert suite.checks[0].generated and not active.checks[0].generated
    assert active.checks[0].spec.model_dump(exclude={"generated"}) == (
        suite.checks[0].spec.model_dump(exclude={"generated"})
    )


@pytest.mark.parametrize("uri", ["bolt://example.com:7687", "neo4j+s://host.databases.neo4j.io"])
def test_external_database_is_rejected(uri):
    with pytest.raises(ValueError, match="loopback"):
        benchmark.connection(uri)


@pytest.mark.parametrize(
    ("text", "valid", "loads"),
    [
        ("suite: example\nsuite: duplicate\n", False, False),
        (SUITE.replace("equals: [1500]", "equals: [] , empty: false"), True, False),
        (SUITE.replace("generated: true", "generated: 'true'"), False, False),
    ],
)
def test_schema_and_loader_are_independent_and_failed_load_never_executes(
    tmp_path,
    text,
    valid,
    loads,
):
    class NoRun:
        def run_suite(self, *args, **kwargs):
            pytest.fail("an invalid submission reached execution")

    path = tmp_path / "example.yml"
    path.write_text(text, encoding="utf-8")
    validator = Draft202012Validator(
        benchmark.read_json(benchmark.BENCHMARK / "context/check.schema.json"),
        format_checker=FormatChecker(),
    )
    task = {"id": "example", "expected_verdict": "pass"}
    row, result = benchmark.score_one(path, task, validator, NoRun())
    assert (row["valid"], row["loads"], row["runs"], row["correct"]) == (valid, loads, False, False)
    assert result is None and path.read_text(encoding="utf-8") == text


def test_frozen_context_and_answer_key_integrity():
    manifest = benchmark.read_json(benchmark.BENCHMARK / "manifest.json")
    for name, expected in manifest["sha256"].items():
        assert benchmark.digest(ROOT / name) == expected, name
    for name, expected in manifest["fixture_sha256_lf"].items():
        assert benchmark.fixture_digest(ROOT / name) == expected, name
    tasks = benchmark.read_json(benchmark.BENCHMARK / "task-set.json")["intents"]
    context = benchmark.read_json(benchmark.BENCHMARK / "context/intents.json")
    assert len(tasks) == len({task["id"] for task in tasks}) == 10
    assert context == [{"id": task["id"], "intent": task["intent"]} for task in tasks]
    assert "oracle" not in json.dumps(context) and "expected_verdict" not in json.dumps(context)


def test_committed_matrix_matches_raw_submissions_and_validated_engine_results():
    data = benchmark.read_json(benchmark.BENCHMARK / "results/results.json")
    submissions = benchmark.read_json(benchmark.BENCHMARK / "submissions.json")["sha256"]
    assert len(data["rows"]) == len(submissions) == 30 and data["graph_unchanged"]
    assert len({(row["model"], row["intent"]) for row in data["rows"]}) == 30
    for row in data["rows"]:
        model = row["model"].removeprefix("gpt-5.6-")
        name = f"raw/{model}/{row['intent']}.yml"
        assert benchmark.digest(benchmark.BENCHMARK / name) == submissions[name] == row["sha256"]
        result = load_results(benchmark.BENCHMARK / f"results/{model}/{row['intent']}.json")
        assert result.checks[0].verdict.value == row["actual_verdict"]
        assert result.run.graphcheck_version == data["graphcheck"]
        assert row["correct"] == (row["runs"] and row["actual_verdict"] == row["expected_verdict"])
        assert row["generated"] and row["effort"] == "max"


@pytest.mark.skipif(not os.getenv("GRAPHCHECK_AUTHORING_URI"), reason="requires benchmark fixture")
def test_live_remediations_return_intended_verdicts_without_changing_proposals_or_graph():
    path = benchmark.BENCHMARK / "remediations.yml"
    original = path.read_bytes()
    suite = load_suite(original.decode("utf-8"))
    client = Neo4jClient(benchmark.connection(os.environ["GRAPHCHECK_AUTHORING_URI"]))
    try:
        before = benchmark.graph_digest(client)
        results = Engine(client).run_suite(benchmark.activated(suite))
        assert {check.id: check.verdict.value for check in results.checks} == {
            "owner-evidence": "fail",
            "regression-projection": "pass",
            "aggregate-witness": "fail",
            "incoming-sender-scope": "pass",
        }
        assert benchmark.graph_digest(client) == before and path.read_bytes() == original
        assert all(check.generated for check in suite.checks)
    finally:
        client.close()


@pytest.mark.skipif(not os.getenv("GRAPHCHECK_AUTHORING_URI"), reason="requires benchmark fixture")
@pytest.mark.parametrize(
    ("query", "expect", "verdict", "runs", "correct"),
    [
        ("MATCH (c:Customer) RETURN count(c) AS count", "equals: [1507]", "pass", True, True),
        ("MATCH (c:Customer) RETURN c", "rows: {exactly: 1500}", "fail", True, True),
        ("MATCH (c:Customer) RETURN c", "rows: {exactly: 1500}", "pass", True, False),
        ("MATCH (c:Customer) RETURN count(c) AS count", "equals: [1500]", "fail", False, False),
        ("THIS IS NOT CYPHER", "equals: [1]", "pass", False, False),
        (
            "CREATE (:BenchmarkWriteMustBeBlocked) RETURN 1 AS count",
            "equals: [1]",
            "pass",
            False,
            False,
        ),
    ],
)
def test_live_scorer_distinguishes_assertions_execution_errors_and_blocked_writes(
    tmp_path,
    query,
    expect,
    verdict,
    runs,
    correct,
):
    path = tmp_path / "example.yml"
    text = SUITE.replace("MATCH (c:Customer) RETURN count(c) AS count", query)
    path.write_text(text.replace("equals: [1500]", expect), encoding="utf-8")
    original = path.read_bytes()
    client = Neo4jClient(benchmark.connection(os.environ["GRAPHCHECK_AUTHORING_URI"]))
    try:
        before = benchmark.graph_digest(client)
        engine = Engine(client, config=EngineConfig(time_budget_s=30, max_concurrency=1))
        validator = Draft202012Validator(
            benchmark.read_json(benchmark.BENCHMARK / "context/check.schema.json"),
        )
        row, result = benchmark.score_one(
            path,
            {"id": "example", "expected_verdict": verdict},
            validator,
            engine,
        )
        assert (row["valid"], row["loads"], row["runs"], row["correct"]) == (
            True,
            True,
            runs,
            correct,
        )
        assert result is not None and path.read_bytes() == original
        assert benchmark.graph_digest(client) == before
        if query.startswith("CREATE"):
            assert row["errors"][0]["code"] == "neo4j.write_rejected"
    finally:
        client.close()
