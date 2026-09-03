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

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from graphcheck.connection_profiles import ConnectionProfile

collect_ignore = [str(Path(__file__).parent / "fixtures" / "external")]


@dataclass(frozen=True)
class Neo4jTestTarget:
    name: str
    image: str
    cypher: str

    @property
    def enterprise_image(self) -> str:
        return f"{self.image}-enterprise"


NEO4J_TARGETS = (
    Neo4jTestTarget("lts-cypher-5", "neo4j:5.26.28", "5"),
    Neo4jTestTarget("current-cypher-5", "neo4j:2026.06.0", "5"),
    Neo4jTestTarget("current-cypher-25", "neo4j:2026.06.0", "25"),
)
_NEO4J_PASSWORD = "graphora-test"
_NEO4J_RESTRICTED_PASSWORD = "graphora-restricted-test"


def _selected_neo4j_targets() -> tuple[Neo4jTestTarget, ...]:
    requested = os.environ.get("GRAPHCHECK_NEO4J_TARGET")
    selected = tuple(target for target in NEO4J_TARGETS if requested in {None, target.name})
    if requested is not None and not selected:
        raise ValueError(
            f"unknown GRAPHCHECK_NEO4J_TARGET {requested!r}; "
            f"choose one of {', '.join(target.name for target in NEO4J_TARGETS)}"
        )
    return selected


def _configure_cypher(container, target: Neo4jTestTarget):
    if target.image.startswith("neo4j:202"):
        container.with_env("NEO4J_db_query_default__language", f"CYPHER_{target.cypher}")
    return container


@pytest.fixture(autouse=True)
def isolated_telemetry_config(tmp_path: Path, monkeypatch) -> Path:
    """Keep consent, identity, and delivery configuration inside each test."""

    from graphcheck.telemetry import posthog

    config = tmp_path / "telemetry.json"
    monkeypatch.setenv("GRAPHCHECK_TELEMETRY_CONFIG", str(config))
    monkeypatch.delenv("GRAPHCHECK_TELEMETRY", raising=False)
    monkeypatch.delenv("GRAPHCHECK_POSTHOG_API_KEY", raising=False)
    monkeypatch.setattr(posthog, "POSTHOG_PROJECT_API_KEY", None)
    return config


# Container startup dominates these tests; mutating tests must clean their data before teardown.
@pytest.fixture(scope="session", params=_selected_neo4j_targets(), ids=lambda target: target.name)
def neo4j_test_target(request):
    return request.param


@pytest.fixture(scope="session")
def neo4j_profile(neo4j_test_target):
    from testcontainers.neo4j import Neo4jContainer

    container = _configure_cypher(
        Neo4jContainer(neo4j_test_target.image, password=_NEO4J_PASSWORD),
        neo4j_test_target,
    )
    with container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=_NEO4J_PASSWORD,
            database="neo4j",
        )


@pytest.fixture(scope="session")
def neo4j_apoc_profile(neo4j_test_target):
    from testcontainers.neo4j import Neo4jContainer

    container = _configure_cypher(
        Neo4jContainer(neo4j_test_target.image, password=_NEO4J_PASSWORD),
        neo4j_test_target,
    )
    container.with_env("NEO4J_PLUGINS", '["apoc"]')
    container.with_env("NEO4JLABS_PLUGINS", '["apoc"]')
    with container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=_NEO4J_PASSWORD,
            database="neo4j",
        )


@pytest.fixture(scope="session")
def neo4j_enterprise_profiles(neo4j_test_target):
    """Enterprise users covering reader, restricted, custom, and HOME-scoped access."""
    from neo4j import GraphDatabase
    from testcontainers.neo4j import Neo4jContainer

    container = _configure_cypher(
        Neo4jContainer(neo4j_test_target.enterprise_image, password=_NEO4J_PASSWORD),
        neo4j_test_target,
    )
    container.with_env("NEO4J_ACCEPT_LICENSE_AGREEMENT", "yes")
    with container:
        driver = GraphDatabase.driver(
            container.get_connection_url(), auth=("neo4j", _NEO4J_PASSWORD)
        )
        try:
            with driver.session(database="neo4j") as session:
                session.run("CREATE (:Customer {ssn: 'integration-secret'})").consume()
            with driver.session(database="system") as session:
                statements = [
                    "CREATE USER graphcheck_reader SET PASSWORD $password CHANGE NOT REQUIRED",
                    "GRANT ROLE reader TO graphcheck_reader",
                    "CREATE USER graphcheck_restricted SET PASSWORD $password CHANGE NOT REQUIRED",
                    "CREATE USER graphcheck_boosted "
                    "SET PASSWORD $password CHANGE NOT REQUIRED SET HOME DATABASE neo4j",
                    "CREATE ROLE graphcheck_boosted_role",
                    "GRANT ACCESS ON DATABASE neo4j TO graphcheck_boosted_role",
                    "GRANT MATCH {*} ON GRAPH neo4j ELEMENTS * TO graphcheck_boosted_role",
                    "GRANT EXECUTE PROCEDURE * ON DBMS TO graphcheck_boosted_role",
                    "GRANT EXECUTE BOOSTED PROCEDURE * ON DBMS TO graphcheck_boosted_role",
                    "GRANT INDEX MANAGEMENT ON DATABASE neo4j TO graphcheck_boosted_role",
                    "GRANT ROLE graphcheck_boosted_role TO graphcheck_boosted",
                    "CREATE USER graphcheck_home_reader "
                    "SET PASSWORD $password CHANGE NOT REQUIRED SET HOME DATABASE neo4j",
                    "CREATE ROLE graphcheck_home_reader_role",
                    "GRANT MATCH {*} ON HOME GRAPH ELEMENTS * TO graphcheck_home_reader_role",
                    "GRANT ROLE graphcheck_home_reader_role TO graphcheck_home_reader",
                    "CREATE USER graphcheck_home_denied "
                    "SET PASSWORD $password CHANGE NOT REQUIRED SET HOME DATABASE neo4j",
                    "CREATE ROLE graphcheck_home_denied_role",
                    "GRANT MATCH {*} ON GRAPH * ELEMENTS * TO graphcheck_home_denied_role",
                    "DENY READ {ssn} ON HOME GRAPH NODES Customer TO graphcheck_home_denied_role",
                    "GRANT ROLE graphcheck_home_denied_role TO graphcheck_home_denied",
                ]
                for statement in statements:
                    params = (
                        {"password": _NEO4J_RESTRICTED_PASSWORD} if "$password" in statement else {}
                    )
                    session.run(statement, params).consume()
        finally:
            driver.close()

        profiles = {
            user: ConnectionProfile(
                uri=container.get_connection_url(),
                user=user,
                password=_NEO4J_RESTRICTED_PASSWORD,
                database="neo4j",
            )
            for user in (
                "graphcheck_reader",
                "graphcheck_restricted",
                "graphcheck_boosted",
                "graphcheck_home_reader",
                "graphcheck_home_denied",
            )
        }
        profiles["neo4j_admin"] = ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=_NEO4J_PASSWORD,
            database="neo4j",
        )
        yield profiles


@pytest.fixture(scope="session")
def neo4j_restricted_profile(neo4j_enterprise_profiles):
    return neo4j_enterprise_profiles["graphcheck_restricted"]
