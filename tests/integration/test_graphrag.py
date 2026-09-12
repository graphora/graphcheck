"""GraphRAG acceptance against the hostile builder and fraud-ring fixtures."""

import os
import runpy
from pathlib import Path

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.packs.graphrag import GRAPHRAG_CHECK_NAMES, GraphRAGModel
from graphcheck.project import GraphRAGPackConfig, ProjectPacksConfig, load_project_config
from tests.integration.test_hostile_graphs import (
    _assert_graphrag_fixture,
    _cli,
    _project,
    _run_payload,
    _seeded_graph,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j tests",
)
HOSTILE = Path(__file__).with_name("hostile")
MODEL = GraphRAGModel(
    document_label="Document",
    chunk_label="Chunk",
    entity_label="__Entity__",
    document_chunk_rel="PART_OF",
    chunk_entity_rel="HAS_ENTITY",
    embedding_property="embedding",
)


def _assert_cli_exit(run, expected):
    output = run.stdout + run.stderr
    assert run.returncode == expected, output
    assert "Traceback (most recent call last)" not in output


def _enable_pack(root, profile, model=MODEL):
    _project(root, profile, "llm-kg-builder.yml")
    config = load_project_config(root)
    config.packs = ProjectPacksConfig(graphrag=GraphRAGPackConfig(model=model))
    (root / "graphcheck.yml").write_text(
        yaml.safe_dump(config.model_dump(exclude_none=True)), encoding="utf-8"
    )


def _write(profile, query):
    with (
        GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
        driver.session(database=profile.database) as session,
    ):
        session.run(query).consume()


@pytest.mark.parametrize("clean", [True, False])
def test_builder_fixture_is_clean_or_reports_each_planted_defect(neo4j_profile, tmp_path, clean):
    _enable_pack(tmp_path, neo4j_profile)
    fixture = "llm-kg-builder-clean.cypher" if clean else "llm-kg-builder.cypher"
    with _seeded_graph(neo4j_profile, (HOSTILE / fixture).read_text(encoding="utf-8")):
        run = _cli(tmp_path, "run", "--suite", "graphrag")
        _assert_cli_exit(run, 0 if clean else 1)
        checks = {check["id"]: check for check in _run_payload(tmp_path)["checks"]}
        _assert_graphrag_fixture(neo4j_profile, checks, clean=clean)


def test_fraud_ring_model_absent_exits_zero_without_errors(neo4j_profile, tmp_path):
    _enable_pack(tmp_path, neo4j_profile)
    external = Path(__file__).parents[1] / "fixtures/external/fraud-ring"
    if not (external / "fixtures/fraud-ring/seed.cypher").exists():
        pytest.skip("initialize the fraud-ring fixture submodule")
    split = runpy.run_path(str(external / "tests/cypher_utils.py"))["split_statements"]
    with _seeded_graph(neo4j_profile):
        for statement in split(
            (external / "fixtures/fraud-ring/seed.cypher").read_text(encoding="utf-8")
        ):
            _write(neo4j_profile, statement)
        run = _cli(tmp_path, "run", "--suite", "graphrag")
        _assert_cli_exit(run, 0)
        checks = _run_payload(tmp_path)["checks"]
        assert len(checks) == len(GRAPHRAG_CHECK_NAMES) and "No checks were evaluated" in run.stdout
        assert all(
            check["skip_reason"] == "model_absent"
            and check["error"] is None
            and check["expected"]["not_evaluated_reason"]
            for check in checks
        )


@pytest.mark.parametrize("relationship", ["PART_OF", "HAS_ENTITY"])
def test_a_wholly_missing_relationship_is_a_defect(neo4j_profile, tmp_path, relationship):
    _enable_pack(tmp_path, neo4j_profile)
    with _seeded_graph(
        neo4j_profile, (HOSTILE / "llm-kg-builder-clean.cypher").read_text(encoding="utf-8")
    ):
        _write(neo4j_profile, f"MATCH ()-[r:{relationship}]->() DELETE r")
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 1)
        checks = {check["id"]: check for check in _run_payload(tmp_path)["checks"]}
        affected = "orphan_chunks" if relationship == "PART_OF" else "entity_without_provenance"
        assert checks[affected]["verdict"] == "fail"
        assert all(check["verdict"] in {"pass", "fail"} for check in checks.values())


def test_wrong_label_links_do_not_supply_provenance(neo4j_profile, tmp_path):
    _enable_pack(tmp_path, neo4j_profile)
    with _seeded_graph(
        neo4j_profile, (HOSTILE / "llm-kg-builder-clean.cypher").read_text(encoding="utf-8")
    ):
        _write(
            neo4j_profile,
            "MATCH ()-[r:HAS_ENTITY]->(entity:__Entity__ {id: 'Ada Lovelace'}) "
            "DELETE r CREATE (:NotAChunk)-[:HAS_ENTITY]->(entity)",
        )
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 1)
        checks = {check["id"]: check for check in _run_payload(tmp_path)["checks"]}
        assert checks["entity_without_provenance"]["measured"]["violations"] == 1
        assert checks["dangling_extraction_relationships"]["measured"]["violations"] == 2


def test_custom_reversed_model_and_multiple_documents_are_supported(neo4j_profile, tmp_path):
    model = GraphRAGModel(
        document_label="Source File",
        chunk_label="Text`Unit",
        entity_label="Concept",
        document_chunk_rel="in file",
        chunk_entity_rel="from text",
        embedding_property="vector",
        name_property="display name",
        document_chunk_direction="in",
        chunk_entity_direction="in",
        extraction_rel_types=["connects"],
    )
    _enable_pack(tmp_path, neo4j_profile, model)
    cypher = """CREATE (d:`Source File`), (d2:`Source File`), (c:`Text``Unit` {vector: [1.0, 2.0]}),
      (a:Concept {`display name`: 'Ada'}), (b:Concept {`display name`: 'Grace'}),
      (c)-[:`in file`]->(d), (c)-[:`in file`]->(d2),
      (a)-[:`from text`]->(c), (b)-[:`from text`]->(c), (a)-[:connects]->(b)"""
    with _seeded_graph(neo4j_profile, cypher):
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 0)
        assert all(check["verdict"] == "pass" for check in _run_payload(tmp_path)["checks"])


@pytest.mark.parametrize(
    ("value", "defect", "dimension"),
    [
        ("null", "missing", None),
        ("'bad'", "invalid_type", None),
        ("42", "invalid_type", None),
        ("['1', '2']", "invalid_type", None),
        ("[]", "empty", 0),
        ("[0.0, 0.0]", "zero", 2),
        ("[1.0, 0.0 / 0.0]", "nan", 2),
        ("[1, 2, 3]", "wrong_dimension", 3),
    ],
)
def test_embedding_defect_types_and_all_invalid_population(
    neo4j_profile, tmp_path, value, defect, dimension
):
    _enable_pack(tmp_path, neo4j_profile)
    with _seeded_graph(
        neo4j_profile,
        "CREATE (:Document), (:__Entity__) "
        "CREATE (:Chunk {embedding: [1, 2]}), (:Chunk {embedding: [1.0, 2.0]})",
    ):
        _write(neo4j_profile, f"CREATE (:Chunk {{id: 'bad', embedding: {value}}})")
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 1)
        result = next(
            check
            for check in _run_payload(tmp_path)["checks"]
            if check["id"] == "embedding_consistency"
        )
        assert result["verdict"] == "fail", result["error"]
        assert result["measured"]["expected_dimension"] == 2
        finding = result["measured"]["findings"][0]
        assert (finding["defect"], finding["dimension"]) == (defect, dimension)
        if defect == "wrong_dimension":
            return
        _write(neo4j_profile, "MATCH (n:Chunk) WHERE n.id IS NULL DELETE n")
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 1)
        result = next(
            check
            for check in _run_payload(tmp_path)["checks"]
            if check["id"] == "embedding_consistency"
        )
        assert result["verdict"] == "fail", result["error"]
        assert result["measured"]["expected_dimension"] is None
        assert result["measured"]["violations"] == 1


def test_embedding_dimension_ties_and_capped_evidence(neo4j_profile, tmp_path):
    _enable_pack(tmp_path, neo4j_profile)
    with _seeded_graph(
        neo4j_profile,
        "CREATE (:Document), (:__Entity__) "
        "WITH 1 AS ignored UNWIND range(1, 240) AS i "
        "CREATE (:Chunk {embedding: CASE WHEN i % 2 = 0 THEN [1, 2, 3] "
        "ELSE [1, 2] END})",
    ):
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 1)
        result = next(
            check
            for check in _run_payload(tmp_path)["checks"]
            if check["id"] == "embedding_consistency"
        )
        assert result["verdict"] == "fail", result["error"]
        assert result["measured"]["expected_dimension"] == 2
        assert result["measured"]["violations"] == 120
        assert len(result["measured"]["findings"]) == 100 and result["evidence"]["truncated"]
