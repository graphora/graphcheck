import os
from pathlib import Path

import pytest
from load_graph import load_graph
from neo4j import GraphDatabase

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


def clear_database(driver, database):
    with driver.session(database=database) as session:
        session.run("MATCH (n) DETACH DELETE n")


@pytest.mark.parametrize(
    (
        "fixture_file",
        "expected_customers",
        "expected_nodes",
    ),
    [
        (
            "fraud-ring.baseline.cypher",
            1507,
            5011,
        ),
        (
            "fraud-ring.cypher",
            1327,
            4831,
        ),
    ],
)
def test_fixture_counts(
    neo4j_profile,
    fixture_file,
    expected_customers,
    expected_nodes,
):
    stats = load_graph(
        neo4j_profile,
        Path("tests/fixtures") / fixture_file,
    )
    assert stats.nodes == expected_nodes
    assert stats.relationships > 0
    assert stats.statements > 0

    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )

    try:
        with driver.session(database=neo4j_profile.database) as session:
            customer_count = session.run("MATCH (c:Customer) RETURN count(c) AS total").single()[
                "total"
            ]

            account_count = session.run("MATCH (a:Account) RETURN count(a) AS total").single()[
                "total"
            ]

            transaction_count = session.run(
                "MATCH (t:Transaction) RETURN count(t) AS total"
            ).single()["total"]
            assert customer_count == expected_customers
            assert account_count == 2504
            assert transaction_count == 1000

    finally:
        driver.close()


def test_planted_defects(neo4j_profile):
    """
    Verify the planted defects are still present in the fixture graph.
    """

    stats = load_graph(
        neo4j_profile,
        Path("tests/fixtures/fraud-ring.cypher"),
    )
    assert stats.nodes == 4831
    assert stats.relationships > 0
    assert stats.statements > 0

    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )

    try:
        with driver.session(database=neo4j_profile.database) as session:
            # Count orphan Account nodes
            orphan_count = session.run("""
                MATCH (a:Account)
                WHERE NOT (a)--()
                RETURN count(a) AS total
            """).single()["total"]

            # Count Accounts owned by more than one Customer
            cardinality_count = session.run("""
                MATCH (c:Customer)-[:OWNS]->(a:Account)
                WITH a, count(c) AS owners
                WHERE owners > 1
                RETURN count(a) AS total
            """).single()["total"]
        assert orphan_count == 3
        assert cardinality_count == 1

    finally:
        driver.close()


def test_drift_acceptance(neo4j_profile):
    """
    Verify the documented customer-count drift between the
    baseline and current fixture graphs.
    """
    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )

    try:
        # Load the baseline fixture
        load_graph(
            neo4j_profile,
            Path("tests/fixtures/fraud-ring.baseline.cypher"),
        )

        with driver.session(database=neo4j_profile.database) as session:
            baseline_customers = session.run(
                "MATCH (c:Customer) RETURN count(c) AS total"
            ).single()["total"]
        clear_database(driver, neo4j_profile.database)
        # Load the current fixture
        load_graph(
            neo4j_profile,
            Path("tests/fixtures/fraud-ring.cypher"),
        )

        with driver.session(database=neo4j_profile.database) as session:
            current_customers = session.run("MATCH (c:Customer) RETURN count(c) AS total").single()[
                "total"
            ]

        assert baseline_customers == 1507
        assert current_customers == 1327

        # Verify the documented customer-count drift
        assert baseline_customers - current_customers == 180

    finally:
        driver.close()
