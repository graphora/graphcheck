from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from graphcheck.application.suites import load_suite_inputs
from graphcheck.cli import app
from graphcheck.connection_profiles import write_default_profiles
from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import Capabilities, Results, ResultsTarget, SkipReason, Verdict
from graphcheck.contracts.schemas import validate_check_schema, validate_pack_metadata_schema
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.evaluator import VerdictEvaluator
from graphcheck.engine.graphrag_pack import duplicate_groups, model_presence_query, normalized_name
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.engine.sampling import SamplingPolicy
from graphcheck.errors import GraphCheckError
from graphcheck.packs.catalog import builtin_pack_catalog
from graphcheck.packs.graphrag import GRAPHRAG_CHECK_NAMES, GraphRAGModel
from graphcheck.packs.metadata import load_pack_metadata_yaml
from graphcheck.project import (
    GraphRAGPackConfig,
    ProjectPacksConfig,
    load_project_config,
    write_default_project,
)
from graphcheck.reporting.presentation import present_check, present_results
from graphcheck.telemetry.collector import TelemetryCollector
from graphcheck.telemetry.events import CheckProcessed, EngineFaulted, RunFinished

MODEL = dict(
    document_label="Document",
    chunk_label="Chunk",
    entity_label="__Entity__",
    document_chunk_rel="PART_OF",
    chunk_entity_rel="HAS_ENTITY",
    embedding_property="embedding",
)
TARGET = ResultsTarget(
    database="neo4j",
    server_version="5.26",
    edition="community",
    fingerprint="sha256:graphrag",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)


def suite(names=GRAPHRAG_CHECK_NAMES, model=MODEL, **options):
    return load_suite(
        yaml.safe_dump(
            {
                "suite": "test-graphrag",
                "conformance": [
                    {"id": name, "check": name, "with": {**model, **options}} for name in names
                ],
            }
        )
    )


class Client:
    def __init__(self, *, missing=(), row=None, evidence=(), error=None):
        self.missing, self.row, self.evidence, self.error = missing, row, evidence, error
        self.calls = []

    def run_read(self, query, params, *, timeout_s=None, allow_missing_schema=False):
        self.calls.append((query, params, allow_missing_schema))
        if self.error:
            raise self.error
        if "count_0" in query:
            return [{"missing_labels": list(self.missing)}]
        if "violation_count" not in query and "candidates" not in query:
            return [{"evidence": list(self.evidence)}]
        return [self.row]


def candidate(node_id, name):
    return {
        "node_id": node_id,
        "name": name,
        "pointer": {"kind": "node", "id": node_id, "labels": ["__Entity__"]},
    }


def test_manifest_and_all_check_payloads_have_schema_parity():
    path = Path(__file__).parents[3] / "src/graphcheck/packs/graphrag.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    metadata = load_pack_metadata_yaml(yaml.safe_dump(raw))
    validate_pack_metadata_schema(raw)
    for name, check in metadata.checks.items():
        assert check.catches and check.does_not_catch and check.requires == ["read"]
        assert check.evidence.id_fields and check.evidence.elements
        assert builtin_pack_catalog().checks[name].pack == "graphrag"
        validate_check_schema(
            {"suite": "g", "conformance": [{"id": name, "check": name, "with": MODEL}]}
        )
        raw["checks"][name]["sampled"] = not check.sampled
        with pytest.raises(ValidationError):
            load_pack_metadata_yaml(yaml.safe_dump(raw))
        with pytest.raises(jsonschema.ValidationError):
            validate_pack_metadata_schema(raw)
        raw["checks"][name]["sampled"] = check.sampled


@pytest.mark.parametrize("missing", [("Document", "Chunk", "__Entity__"), ("Chunk",)])
def test_absent_model_skips_every_check_with_reason_and_exit_zero(missing):
    client = Client(missing=missing)
    results = Engine(client).run_suite(suite(), target=TARGET)
    assert results.run.exit_code == 0 and results.run.run_status == "complete"
    assert results.score is None and results.totals.skipped == len(GRAPHRAG_CHECK_NAMES)
    for check in results.checks:
        assert check.verdict is Verdict.SKIPPED and check.skip_reason is SkipReason.MODEL_ABSENT
        assert check.error is None and check.measured is None
        assert missing[0] in present_check(check).skip_reason.explanation
        assert present_check(check).evaluation_label == "Not evaluated"
    assert len(client.calls) == len(GRAPHRAG_CHECK_NAMES) and all(call[2] for call in client.calls)
    assert "No checks were evaluated" in present_results(results).primary_sentence
    Results.model_validate_json(results.model_dump_json(by_alias=True))


def test_unconfigured_model_never_submits_a_check_query():
    client = Client()
    results = Engine(client).run_suite(suite(model={}), target=TARGET)
    assert results.run.exit_code == 0 and not client.calls
    assert all(
        "not configured" in check.expected["not_evaluated_reason"] for check in results.checks
    )


def test_connection_errors_remain_errors():
    client = Client(error=GraphCheckError("test.denied", "Read denied", "Use a reader"))
    results = Engine(client).run_suite(suite(), target=TARGET)
    assert results.run.exit_code == 1
    assert all(check.verdict is Verdict.ERRORED for check in results.checks)


def test_absent_model_telemetry_reconciles_without_emitting_model_details():
    collector = TelemetryCollector()
    results = Engine(Client(missing=("Document",)), event_sink=collector).run_suite(
        suite(), target=TARGET
    )
    assert results.run.exit_code == 0
    assert not any(isinstance(event, EngineFaulted) for event in collector.events)
    assert len([event for event in collector.events if isinstance(event, CheckProcessed)]) == len(
        GRAPHRAG_CHECK_NAMES
    )
    finished = next(event for event in collector.events if isinstance(event, RunFinished))
    assert (
        finished.skipped_unsupported_count == len(GRAPHRAG_CHECK_NAMES)
        and finished.engine_error_count == 0
    )


def test_malformed_model_preflight_is_an_error():
    result = Engine(Client(missing=("not-a-configured-label",))).run_suite(suite(), target=TARGET)
    assert all(check.verdict is Verdict.ERRORED for check in result.checks)


def test_cli_absent_model_reports_not_evaluated_and_exits_zero(tmp_path, monkeypatch):
    from graphcheck.neo4j_adapter import QueryResult
    from tests.unit.cli.test_run_cli import FakeClient

    class MissingModelClient(FakeClient):
        def run_read_result(self, query, params, **kwargs):
            return QueryResult(
                [{"missing_labels": ["Document", "Chunk", "__Entity__"]}], ("missing_labels",), ()
            )

    write_default_project(tmp_path, graphrag=True)
    write_default_profiles(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", lambda *args, **kwargs: MissingModelClient())
    run = CliRunner().invoke(app, ["run", "--suite", "graphrag"])
    assert run.exit_code == 0, run.output
    assert "No checks were evaluated" in run.output and "model_absent" in run.output
    assert "Traceback" not in run.output
    report = (tmp_path / ".graphcheck/runs/latest/report.html").read_text(encoding="utf-8")
    assert "GraphRAG model is absent" in report and "Not evaluated" in report


@pytest.mark.parametrize("name", GRAPHRAG_CHECK_NAMES[:3])
def test_provenance_failure_retains_paths_and_element_ids(name):
    record = {
        "node_id": "n:1",
        "missing_path": "(Chunk)-[:HAS_ENTITY]->(__Entity__)",
        "pointer": {"kind": "node", "id": "n:1", "labels": ["__Entity__"]},
    }
    if name == "dangling_extraction_relationships":
        record.update(
            rel_id="r:1",
            source_id="n:1",
            target_id="n:2",
            missing_node_ids=["n:2"],
            pointer={"kind": "rel", "id": "r:1", "type": "KNOWS"},
        )
    client = Client(
        row={"schema_ok": True, "population": 10, "violation_count": 1}, evidence=[record]
    )
    result = Engine(client).run_suite(suite([name]), target=TARGET).checks[0]
    assert result.verdict is Verdict.FAIL, result.error
    assert record["missing_path"] in result.evidence.message
    assert result.measured["findings"][0]["missing_path"] == record["missing_path"]
    assert record["pointer"]["id"] in {pointer.id for pointer in result.evidence.elements}
    assert all(call[2] for call in client.calls)


@pytest.mark.parametrize("name", GRAPHRAG_CHECK_NAMES[:3])
def test_clean_provenance_does_not_collect_evidence(name):
    client = Client(row={"schema_ok": True, "population": 10, "violation_count": 0})
    result = Engine(client).run_suite(suite([name]), target=TARGET).checks[0]
    assert result.verdict is Verdict.PASS and result.evidence is None
    assert len(client.calls) == 2


def test_custom_model_is_bound_and_init_scaffolds_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def offline(*args):
        raise GraphCheckError("test.offline", "No database", "Configure a profile")

    monkeypatch.setattr("graphcheck.cli.init_trace", offline)
    assert CliRunner().invoke(app, ["init", "--pack", "graphrag"]).exit_code == 0
    config = load_project_config(tmp_path)
    assert config.packs.graphrag.model.embedding_property == "embedding"
    config.packs.graphrag.model.entity_label = "Custom Entity"
    checks = load_suite_inputs(tmp_path / "checks", ["graphrag"], config.packs)[0].suite.checks
    assert len(checks) == len(GRAPHRAG_CHECK_NAMES)
    assert all(check.spec.with_["entity_label"] == "Custom Entity" for check in checks)


def test_pack_selection_and_model_changes_affect_suite_hash(tmp_path):
    write_default_project(tmp_path)
    packs = ProjectPacksConfig(graphrag=GraphRAGPackConfig(model=GraphRAGModel(**MODEL)))
    first = load_suite_inputs(tmp_path / "checks", ["graphrag"], packs)[0]
    packs.graphrag.model.entity_label = "Other"
    second = load_suite_inputs(tmp_path / "checks", ["graphrag"], packs)[0]
    assert first.source_sha != second.source_sha
    assert not load_suite_inputs(tmp_path / "checks", ["unselected"], packs)
    packs.graphrag.enabled = False
    assert not load_suite_inputs(tmp_path / "checks", [], packs)


def test_identifiers_and_directions_are_escaped_without_rewriting_tokens():
    model = {
        **MODEL,
        "chunk_label": "Odd`Chunk(n)",
        "chunk_entity_rel": "(n)[r:`",
        "chunk_entity_direction": "in",
        "document_chunk_direction": "in",
    }
    compiler = CypherCompiler()
    plans = [compiler.compile(check) for check in suite(model=model).checks]
    assert "`Odd``Chunk(n)`" in plans[1].query
    assert "(n)-[r:`(n)[r:```]->(other:`Odd``Chunk(n)`)" in plans[1].query
    assert "(source)-[:`(n)[r:```]->(other:`Odd``Chunk(n)` )".replace("` )", "`)") in plans[2].query
    assert "(n)-[r:`PART_OF`]->(other:`Document`)" in plans[0].query
    query, params = model_presence_query(GraphRAGModel(**model))
    assert "`Odd``Chunk(n)`" in query and params["labels"][1] == model["chunk_label"]


@pytest.mark.parametrize(
    "options",
    [
        {"sample_size": 0},
        {"sample_size": 2001},
        {"sample_size": True},
        {"threshold": 0},
        {"threshold": 1.1},
        {"threshold": float("nan")},
    ],
)
def test_duplicate_bounds_are_strict(options):
    with pytest.raises(ValidationError):
        suite(["near_duplicate_entities"], **options)


def test_normalization_similarity_threshold_and_grouping():
    assert normalized_name(" Ａda—LoVELACE \t") == "adalovelace"
    candidates = [
        candidate(str(i), name)
        for i, name in enumerate(
            ["Ada Lovelace", " ADA-LOVELACE ", "Ada Lovelaces", "Grace Hopper", "---", " "]
        )
    ]
    assert duplicate_groups(candidates, 0.9)[0]["node_ids"] == ["0", "1", "2"]
    assert duplicate_groups(candidates, 1)[0]["node_ids"] == ["0", "1"]
    assert duplicate_groups(list(reversed(candidates)), 0.9) == duplicate_groups(candidates, 0.9)
    # A threshold of 1 disables fuzzy matching even for equal bigram multisets.
    assert not duplicate_groups([candidate("a", "abaca"), candidate("b", "acaba")], 1)
    assert not duplicate_groups([candidate("a", "abcd"), candidate("b", "abce")], 2 / 3)
    assert duplicate_groups([candidate("a", "abcd"), candidate("b", "abce")], 0.66)


def test_duplicate_runtime_reports_sample_groups_and_capped_pointers():
    candidates = [
        candidate("n:1", "Ada Lovelace"),
        candidate("n:2", "ADA-LOVELACE"),
        candidate("n:3", "Grace Hopper"),
    ]
    client = Client(
        row={"schema_ok": True, "population": 265214, "sample_size": 3, "candidates": candidates}
    )
    engine = Engine(
        client, config=EngineConfig(evidence_cap=1, sampling=SamplingPolicy(0, 3, "seed"))
    )
    result = engine.run_suite(
        suite(["near_duplicate_entities"], sample_size=3), target=TARGET
    ).checks[0]
    assert result.verdict is Verdict.FAIL, result.error
    assert result.estimate.sample_size == 3 and result.estimate.ci is None
    assert result.measured["findings"][0] == {
        "normalized_key": "adalovelace",
        "normalized_keys": ["adalovelace"],
        "node_ids": ["n:1", "n:2"],
    }
    assert "sample_size=3" in result.evidence.message and "n:2" in result.evidence.message
    assert len(result.evidence.elements) == 1 and result.evidence.truncated
    assert "LIMIT $sample_size" in client.calls[1][0]


def test_duplicate_sampling_seed_is_stable_and_empty_sample_is_exact():
    check = suite(["near_duplicate_entities"]).checks[0]
    compiler = CypherCompiler()
    plan = compiler.compile(check, sample_seed=42)
    assert plan.params == compiler.compile(check, sample_seed=42).params
    assert plan.params != compiler.compile(check, sample_seed=43).params
    assert "ORDER BY rank, elementId(n) LIMIT $sample_size" in plan.query
    result = VerdictEvaluator().evaluate(
        plan, [{"schema_ok": True, "population": 0, "sample_size": 0, "candidates": []}]
    )
    assert result.passed and result.estimate is False


@pytest.mark.parametrize(
    "row",
    [
        {"population": 2, "sample_size": 0, "candidates": []},
        {"population": 2, "sample_size": 1, "candidates": []},
        {
            "population": 2,
            "sample_size": 2,
            "candidates": [candidate("same", "A"), candidate("same", "A")],
        },
        {"population": 1, "sample_size": 1, "candidates": [candidate("a", "A" * 257)]},
    ],
)
def test_invalid_duplicate_samples_never_pass(row):
    plan = CypherCompiler().compile(suite(["near_duplicate_entities"]).checks[0])
    with pytest.raises(GraphCheckError):
        VerdictEvaluator().evaluate(plan, [{"schema_ok": True, **row}])


def test_sampled_clean_result_keeps_sampling_notice():
    plan = CypherCompiler().compile(suite(["near_duplicate_entities"]).checks[0])
    plan = replace(plan, params={**plan.params, "sample_size": 1})
    result = VerdictEvaluator().evaluate(
        plan,
        [
            {
                "schema_ok": True,
                "population": 265214,
                "sample_size": 1,
                "candidates": [candidate("n:1", "Ada")],
            }
        ],
    )
    assert result.passed and result.estimate.sample_size == 1
    assert "no population duplicate rate" in result.measured["completeness_notice"]


@pytest.mark.parametrize(
    ("defect", "dimension"),
    [
        ("missing", None),
        ("invalid_type", None),
        ("empty", 0),
        ("nan", 4),
        ("zero", 4),
        ("wrong_dimension", 3),
    ],
)
def test_embedding_findings_retain_defect_dimension_and_element_id(defect, dimension):
    record = {
        "node_id": "chunk:1",
        "dimension": dimension,
        "defect": defect,
        "pointer": {"kind": "node", "id": "chunk:1", "labels": ["Chunk"]},
    }
    client = Client(
        row={"schema_ok": True, "population": 10, "violation_count": 1, "expected_dimension": 4},
        evidence=[record],
    )
    result = Engine(client).run_suite(suite(["embedding_consistency"]), target=TARGET).checks[0]
    assert result.verdict is Verdict.FAIL, result.error
    assert result.measured["findings"] == [
        {"node_id": "chunk:1", "dimension": dimension, "defect": defect}
    ]
    assert result.measured["expected_dimension"] == 4 and result.estimate is False
    assert result.evidence.elements[0].id == "chunk:1"
    assert defect in result.evidence.message and len(client.calls) == 3


def test_clean_embeddings_do_not_fetch_evidence_or_vectors():
    client = Client(
        row={
            "schema_ok": True,
            "population": 265214,
            "violation_count": 0,
            "expected_dimension": 1536,
        }
    )
    result = Engine(client).run_suite(suite(["embedding_consistency"]), target=TARGET).checks[0]
    assert result.verdict is Verdict.PASS and result.evidence is None
    assert result.measured["population"] == 265214 and len(client.calls) == 2
    plan = CypherCompiler().compile(suite(["embedding_consistency"]).checks[0])
    assert not plan.sampled and "LIMIT" not in plan.query.rsplit("MATCH", 1)[1]
    assert "collect(" not in plan.query
    assert "ORDER BY frequency DESC, dimension LIMIT 1" in plan.query
    assert "LIMIT $evidence_cap" in plan.evidence_query
    assert "vector" not in plan.evidence_query.rsplit("RETURN", 1)[1]
    escaped = CypherCompiler().compile(
        suite(["embedding_consistency"], model={**MODEL, "embedding_property": "vec`tor"}).checks[0]
    )
    assert "n.`vec``tor`" in escaped.query and "n.`vec``tor`" in escaped.evidence_query


def test_embedding_evidence_is_capped_without_capping_violation_count():
    plan = CypherCompiler(evidence_cap=1).compile(suite(["embedding_consistency"]).checks[0])
    result = VerdictEvaluator().evaluate(
        plan,
        [
            {
                "schema_ok": True,
                "population": 100,
                "violation_count": 100,
                "expected_dimension": None,
                "evidence": [
                    {
                        "node_id": "n:1",
                        "dimension": None,
                        "defect": "missing",
                        "pointer": {"kind": "node", "id": "n:1", "labels": ["Chunk"]},
                    }
                ],
            }
        ],
    )
    assert not result.passed and result.measured["violations"] == 100
    assert result.evidence.truncated and len(result.evidence.elements) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"violation_count": 11},
        {"expected_dimension": -1},
        {"expected_dimension": True},
        {"evidence": []},
        {"evidence": [{"node_id": "n:1", "defect": "zero"}]},
        {"evidence": [{"node_id": "n:1", "defect": "zero", "dimension": -1}]},
    ],
)
def test_embedding_malformed_results_are_errors(patch):
    plan = CypherCompiler().compile(suite(["embedding_consistency"]).checks[0])
    with pytest.raises(GraphCheckError):
        VerdictEvaluator().evaluate(
            plan,
            [
                {
                    "schema_ok": True,
                    "population": 10,
                    "violation_count": 1,
                    "expected_dimension": 4,
                    "evidence": [],
                    **patch,
                }
            ],
        )
