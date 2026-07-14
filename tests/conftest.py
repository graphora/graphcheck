"""Shared test fixtures.

The Neo4j container fixtures below are shared by the connector integration tests
(``tests/integration/``) and the fixture-graph tests (``tests/fixtures/``), so the
Neo4j harness is defined once. They spin up real containers via testcontainers, so
gate any module that uses them with::

    pytestmark = pytest.mark.skipif(
        os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
        reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
    )
"""

from __future__ import annotations

import pytest

from graphcheck.connection_profiles import ConnectionProfile

NEO4J_IMAGES = ["neo4j:4.4", "neo4j:5"]
_NEO4J_PASSWORD = "graphora-test"
_NEO4J_RESTRICTED_PASSWORD = "graphora-restricted-test"


@pytest.fixture(params=NEO4J_IMAGES)
def neo4j_profile(request):
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(request.param, password=_NEO4J_PASSWORD) as container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=_NEO4J_PASSWORD,
            database="neo4j",
        )


@pytest.fixture(params=NEO4J_IMAGES)
def neo4j_apoc_profile(request):
    from testcontainers.neo4j import Neo4jContainer

    container = Neo4jContainer(request.param, password=_NEO4J_PASSWORD)
    container.with_env("NEO4J_PLUGINS", '["apoc"]')
    container.with_env("NEO4JLABS_PLUGINS", '["apoc"]')
    with container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=_NEO4J_PASSWORD,
            database="neo4j",
        )


@pytest.fixture
def neo4j_restricted_profile():
    """Enterprise user with PUBLIC access/procedures but no graph read privileges."""
    from neo4j import GraphDatabase
    from testcontainers.neo4j import Neo4jContainer

    container = Neo4jContainer("neo4j:5-enterprise", password=_NEO4J_PASSWORD)
    container.with_env("NEO4J_ACCEPT_LICENSE_AGREEMENT", "yes")
    with container:
        driver = GraphDatabase.driver(
            container.get_connection_url(), auth=("neo4j", _NEO4J_PASSWORD)
        )
        try:
            with driver.session(database="system") as session:
                session.run(
                    "CREATE USER graphcheck_restricted SET PASSWORD $password CHANGE NOT REQUIRED",
                    password=_NEO4J_RESTRICTED_PASSWORD,
                ).consume()
        finally:
            driver.close()

        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="graphcheck_restricted",
            password=_NEO4J_RESTRICTED_PASSWORD,
            database="neo4j",
        )
