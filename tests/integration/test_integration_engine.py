import os

import pytest
from neo4j import GraphDatabase

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import Verdict
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.pii_pack import _SAMPLE_NODE_MULTIPLIER
from graphcheck.engine.runner import Engine
from graphcheck.engine.sampling import (
    CYPHER_SAMPLE_MODULUS,
    cypher_hash_parameters,
    cypher_hash_value,
)
from graphcheck.neo4j_adapter import Neo4jClient

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


def test_engine_executes_parameterized_and_temporal_competency_queries(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        results = Engine(client).run_yaml(
            """
suite: integration
competency:
  - id: answer
    question: Does a parameter round-trip?
    query: RETURN $answer AS answer
    params: {answer: 42}
    expect: {rows: {exactly: 1}, columns: [answer], equals: [42]}
  - id: date-equality
    question: Does a Neo4j date equal the pinned YAML date?
    query: RETURN date('2026-01-01') AS as_of
    expect: {rows: {exactly: 1}, columns: [as_of], equals: [2026-01-01]}
"""
        )
    finally:
        client.close()

    assert [check.verdict for check in results.checks] == [Verdict.PASS, Verdict.PASS]
    assert results.checks[0].compiled_query == "RETURN $answer AS answer"
    assert results.checks[0].params == {"answer": 42}
    assert results.checks[1].measured["equals"] is True


def test_broken_query_is_errored_and_later_check_still_runs(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        results = Engine(client).run_yaml(
            """
suite: isolation
competency:
  - id: broken
    question: Is broken Cypher isolated?
    query: THIS IS NOT CYPHER
    expect: {rows: {exactly: 1}}
  - id: healthy
    question: Does the next check still run?
    query: RETURN 1 AS value
    expect: {rows: {exactly: 1}, columns: [value]}
"""
        )
    finally:
        client.close()

    assert [check.verdict for check in results.checks] == [Verdict.ERRORED, Verdict.PASS]
    assert results.checks[0].error.code == "neo4j.query_failed"


def test_empty_graph_vacuously_measures_real_compiled_conformance_query(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        results = Engine(client).run_yaml(
            """
suite: missing-schema
conformance:
  - id: typo
    check: completeness
    with: {label: ThisLabelMustNotExist, property: id}
"""
        )
    finally:
        client.close()

    check = results.checks[0]
    assert results.run.target.nodes == 0
    assert results.run.target.relationships == 0
    assert "CALL db.labels()" in check.compiled_query
    assert check.verdict is Verdict.PASS
    assert check.measured["population"] == 0


def test_populated_graph_with_unfamiliar_schema_is_errored(neo4j_profile):
    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )
    try:
        with driver.session(database=neo4j_profile.database) as session:
            session.run("CREATE (:ExistingLabel)").consume()
    finally:
        driver.close()

    client = Neo4jClient(neo4j_profile)
    try:
        results = Engine(client).run_yaml(
            """
suite: missing-schema
conformance:
  - id: typo
    check: completeness
    with: {label: ThisLabelMustNotExist, property: id}
"""
        )
    finally:
        client.close()

    check = results.checks[0]
    assert results.run.target.nodes == 1
    assert check.verdict is Verdict.ERRORED
    assert check.error.code == "engine.schema_reference_missing"


def test_customer_authored_write_query_is_rejected_by_read_session(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        results = Engine(client).run_yaml(
            """
suite: write-guard
competency:
  - id: write-attempt
    question: Is a write rejected?
    query: CREATE (:GraphCheckEngineWriteProbe) RETURN 1 AS value
    expect: {rows: {exactly: 1}}
"""
        )
        count = client.run_read(
            "MATCH (n) WHERE 'GraphCheckEngineWriteProbe' IN labels(n) RETURN count(n) AS count"
        )[0]["count"]
    finally:
        client.close()

    assert results.checks[0].verdict is Verdict.ERRORED
    assert results.checks[0].error.code == "neo4j.write_rejected"
    assert count == 0


def test_pii_pack_executes_name_and_value_checks_with_real_cypher(neo4j_profile):
    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )
    try:
        with driver.session(database=neo4j_profile.database) as session:
            session.run(
                "CREATE (:GraphCheckPiiFixture {email: $email, notes: $card, tags: $tags})",
                email="person@example.com",
                card="4111 1111 1111 1111",
                tags=["customer", "priority"],
            ).consume()
    finally:
        driver.close()

    client = Neo4jClient(neo4j_profile)
    try:
        loaded = load_suite(
            """
suite: pii-occurrence-sampling
conformance:
  - id: names
    check: pii_name_match
    with: {label: GraphCheckPiiFixture, sample_size: 1}
"""
        ).checks[0]
        compiled = CypherCompiler().compile(loaded)
        node_id = client.run_read(
            "CYPHER 5 MATCH (n:GraphCheckPiiFixture) RETURN id(n) AS node_id"
        )[0]["node_id"]
        occurrence_base = (node_id % CYPHER_SAMPLE_MODULUS) * _SAMPLE_NODE_MULTIPLIER

        def sample_key(property_index, sample_seed):
            occurrence = (occurrence_base + property_index) % CYPHER_SAMPLE_MODULUS
            return cypher_hash_value(occurrence, cypher_hash_parameters(sample_seed))

        winning_seeds = {}
        seed_step = CYPHER_SAMPLE_MODULUS // 4096
        for sample_seed in range(0, CYPHER_SAMPLE_MODULUS, seed_step):
            winner = min(range(3), key=lambda index: sample_key(index, sample_seed))
            winning_seeds.setdefault(winner, sample_seed)
            if len(winning_seeds) == 3:
                break
        assert set(winning_seeds) == {0, 1, 2}

        sampled_properties = set()
        for sample_seed in winning_seeds.values():
            params = {
                **compiled.params,
                "sample_size": 1,
                **cypher_hash_parameters(sample_seed),
            }
            rows = client.run_read_result(compiled.query, params).rows
            sampled_properties.add(rows[0]["candidates"][0]["property"])

        results = Engine(client).run_yaml(
            """
suite: pii-integration
conformance:
  - id: names
    check: pii_name_match
    with: {label: GraphCheckPiiFixture, patterns: [email]}
  - id: values
    check: pii_value_match
    with:
      label: GraphCheckPiiFixture
      properties: [notes, tags]
      patterns: [credit_card]
  - id: list-type
    check: property_type
    with: {label: GraphCheckPiiFixture, property: tags, type: string}
  - id: list-format
    check: property_format
    with: {label: GraphCheckPiiFixture, property: tags, regex: '^customer$'}
"""
        )
    finally:
        client.close()

    assert [check.verdict for check in results.checks] == [
        Verdict.FAIL,
        Verdict.FAIL,
        Verdict.FAIL,
        Verdict.FAIL,
    ]
    assert all(check.estimate is False for check in results.checks)
    assert all(check.evidence and check.evidence.elements for check in results.checks)
    assert "4111 1111 1111 1111" not in repr(results)
    assert sampled_properties == {"email", "notes", "tags"}
