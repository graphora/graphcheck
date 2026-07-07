import os

import pytest

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)

NEO4J_IMAGES = ["neo4j:4.4", "neo4j:5"]
PASSWORD = "graphora-test"


@pytest.fixture(params=NEO4J_IMAGES)
def neo4j_profile(request):
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(request.param, password=PASSWORD) as container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=PASSWORD,
            database="neo4j",
        )


@pytest.fixture(params=NEO4J_IMAGES)
def neo4j_apoc_profile(request):
    from testcontainers.neo4j import Neo4jContainer

    container = Neo4jContainer(request.param, password=PASSWORD)
    container.with_env("NEO4J_PLUGINS", '["apoc"]')
    container.with_env("NEO4JLABS_PLUGINS", '["apoc"]')
    with container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=PASSWORD,
            database="neo4j",
        )


def test_connect_and_probe(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        client.verify()
        target, visibility, counts = client.probe()

        assert target.server_version
        assert target.edition in {"community", "enterprise"}
        assert target.database == "neo4j"
        assert isinstance(target.capabilities.apoc, bool)
        assert isinstance(target.capabilities.count_store, bool)
        assert visibility.can_connect is True
        assert visibility.can_read is True
        assert counts.nodes >= 0
        assert counts.relationships >= 0
    finally:
        client.close()


def test_read_only_session_rejects_write(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        assert client.run_read("RETURN 1 AS n") == [{"n": 1}]

        with pytest.raises(GraphCheckError) as caught:
            client.run_read("CREATE (:GraphCheckWriteProbe)")

        assert caught.value.error.code in {"neo4j.permission_denied", "neo4j.query_failed"}
    finally:
        client.close()


def test_wrong_password_maps_to_auth_failed(neo4j_profile):
    bad = neo4j_profile.model_copy(update={"password": "wrong-password"})
    client = Neo4jClient(bad)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.verify()

        assert caught.value.error.code == "neo4j.auth_failed"
        assert caught.value.error.fix
    finally:
        client.close()


def test_wrong_database_maps_to_database_not_found(neo4j_profile):
    bad = neo4j_profile.model_copy(update={"database": "missingdb"})
    client = Neo4jClient(bad)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.run_read("RETURN 1 AS n")

        assert caught.value.error.code == "neo4j.database_not_found"
        assert caught.value.error.fix
    finally:
        client.close()


def test_unreachable_maps_to_unreachable():
    profile = ConnectionProfile(
        uri="bolt://127.0.0.1:1",
        user="neo4j",
        password="wrong-password",
        database="neo4j",
    )
    client = Neo4jClient(profile)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.verify()

        assert caught.value.error.code == "neo4j.unreachable"
        assert caught.value.error.fix
    finally:
        client.close()


def test_apoc_absent_on_plain_container(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
        target, _, _ = client.probe()

        assert target.capabilities.apoc is False
    finally:
        client.close()


def test_apoc_present_when_plugin_enabled(neo4j_apoc_profile):
    client = Neo4jClient(neo4j_apoc_profile)
    try:
        target, _, _ = client.probe()

        assert target.capabilities.apoc is True
    finally:
        client.close()
