import os

import pytest
from neo4j import GraphDatabase

from graphcheck.contracts.results import Verdict
from graphcheck.engine.runner import Engine
from graphcheck.neo4j_adapter import Neo4jClient

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


def test_engine_executes_parameterized_competency_query(neo4j_profile):
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
"""
        )
    finally:
        client.close()

    assert results.checks[0].verdict is Verdict.PASS
    assert results.checks[0].compiled_query == "RETURN $answer AS answer"
    assert results.checks[0].params == {"answer": 42}


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


def test_missing_conformance_label_is_errored_not_an_empty_pass(neo4j_profile):
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
    assert count == 0


def test_pii_pack_executes_name_and_value_checks_with_real_cypher(neo4j_profile):
    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )
    try:
        with driver.session(database=neo4j_profile.database) as session:
            session.run(
                "CREATE (:GraphCheckPiiFixture {email: $email, notes: $card})",
                email="person@example.com",
                card="4111 1111 1111 1111",
            ).consume()
    finally:
        driver.close()

    client = Neo4jClient(neo4j_profile)
    try:
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
      properties: [notes]
      patterns: [credit_card]
"""
        )
    finally:
        client.close()

    assert [check.verdict for check in results.checks] == [Verdict.FAIL, Verdict.FAIL]
    assert all(check.estimate is False for check in results.checks)
    assert all(check.evidence and check.evidence.elements for check in results.checks)
    assert "4111 1111 1111 1111" not in repr(results)
