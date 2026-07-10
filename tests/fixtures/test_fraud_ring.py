from neo4j import GraphDatabase

# Neo4j connection details
URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "password"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)


def test_fixture_counts():
    """
    Verify the fixture graph contains the expected number of nodes.
    """

    with driver.session() as session:

        customer_count = session.run(
            "MATCH (c:Customer) RETURN count(c) AS total"
        ).single()["total"]

        account_count = session.run(
            "MATCH (a:Account) RETURN count(a) AS total"
        ).single()["total"]

        transaction_count = session.run(
            "MATCH (t:Transaction) RETURN count(t) AS total"
        ).single()["total"]

    assert customer_count == 1507
    assert account_count == 2504
    assert transaction_count == 1000


def test_planted_defects():
    """
    Verify the planted defects are still present in the fixture graph.
    """

    with driver.session() as session:

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


def teardown_module(module):
    """
    Close the Neo4j driver after all tests complete.
    """
    driver.close()