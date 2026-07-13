import os
from pathlib import Path

import pytest
from load_graph import load_graph
from neo4j import GraphDatabase

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


def test_fixture_counts(neo4j_profile):

    load_graph(
        neo4j_profile,
        Path("tests/fixtures/fraud-ring.cypher"),
    )

    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )

    with driver.session(database=neo4j_profile.database) as session:
        customer_count = session.run("MATCH (c:Customer) RETURN count(c) AS total").single()[
            "total"
        ]

        account_count = session.run("MATCH (a:Account) RETURN count(a) AS total").single()["total"]

        transaction_count = session.run("MATCH (t:Transaction) RETURN count(t) AS total").single()[
            "total"
        ]

    assert customer_count == 1507
    assert account_count == 2504
    assert transaction_count == 1000
    driver.close()


def test_planted_defects(neo4j_profile):
    """
    Verify the planted defects are still present in the fixture graph.
    """

    load_graph(
        neo4j_profile,
        Path("tests/fixtures/fraud-ring.cypher"),
    )

    driver = GraphDatabase.driver(
        neo4j_profile.uri,
        auth=(neo4j_profile.user, neo4j_profile.password),
    )

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
    driver.close()
