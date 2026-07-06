import os

import pytest

from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.profiles import ConnectionProfile

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


@pytest.fixture(params=["neo4j:4.4", "neo4j:5"])
def neo4j_profile(request):
    from testcontainers.neo4j import Neo4jContainer

    password = "graphora-test"
    with Neo4jContainer(request.param, password=password) as container:
        yield ConnectionProfile(
            uri=container.get_connection_url(),
            user="neo4j",
            password=password,
            database="neo4j",
        )


def test_connect_probe_and_read_only_session(neo4j_profile):
    client = Neo4jClient(neo4j_profile)
    try:
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

        with pytest.raises(GraphCheckError):
            client.run_read("CREATE (:GraphCheckWriteProbe)")
    finally:
        client.close()

