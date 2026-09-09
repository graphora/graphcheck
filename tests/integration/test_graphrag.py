"""GraphRAG acceptance against the hostile builder and fraud-ring fixtures."""

import os
import runpy
from pathlib import Path

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.packs.graphrag import GraphRAGModel
from graphcheck.project import GraphRAGPackConfig, ProjectPacksConfig, load_project_config
from tests.integration.test_hostile_graphs import (
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
        assert len(checks) == 4
        assert all(check["verdict"] == ("pass" if clean else "fail") for check in checks.values())
        if clean:
            return
        assert checks["orphan_chunks"]["measured"]["violations"] == 2
        assert checks["entity_without_provenance"]["measured"]["violations"] == 1
        assert checks["dangling_extraction_relationships"]["measured"]["violations"] == 1
        for name in (
            "orphan_chunks",
            "entity_without_provenance",
            "dangling_extraction_relationships",
        ):
            assert checks[name]["evidence"]["elements"]
            assert all(row["missing_path"] for row in checks[name]["measured"]["findings"])
        duplicates = checks["near_duplicate_entities"]
        assert duplicates["measured"]["findings"][0]["normalized_key"] == "adalovelace"
        assert len(duplicates["measured"]["findings"][0]["node_ids"]) == 2
        # Verify the evidence identifies exactly the planted nodes, using live element IDs.
        orphan_ids = [row["node_id"] for row in checks["orphan_chunks"]["measured"]["findings"]]
        with (
            GraphDatabase.driver(
                neo4j_profile.uri, auth=(neo4j_profile.user, neo4j_profile.password)
            ) as driver,
            driver.session(database=neo4j_profile.database) as session,
        ):
            rows = session.run(
                "MATCH (n) WHERE elementId(n) IN $ids RETURN coalesce(n.id, n.fileName) AS name",
                ids=orphan_ids,
            )
            assert {row["name"] for row in rows} == {"chunk-orphan", "no-chunks.txt"}


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
        assert len(checks) == 4 and "No checks were evaluated" in run.stdout
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
    cypher = """CREATE (d:`Source File`), (d2:`Source File`), (c:`Text``Unit`),
      (a:Concept {`display name`: 'Ada'}), (b:Concept {`display name`: 'Grace'}),
      (c)-[:`in file`]->(d), (c)-[:`in file`]->(d2),
      (a)-[:`from text`]->(c), (b)-[:`from text`]->(c), (a)-[:connects]->(b)"""
    with _seeded_graph(neo4j_profile, cypher):
        _assert_cli_exit(_cli(tmp_path, "run", "--suite", "graphrag"), 0)
        assert all(check["verdict"] == "pass" for check in _run_payload(tmp_path)["checks"])
