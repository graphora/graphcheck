import pytest

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import (
    Counts,
    DebugTrace,
    Neo4jClient,
    Visibility,
    _fingerprint,
    _is_apoc_absent_error,
    _plan_has_operator,
    debug_trace,
    error_json,
    map_neo4j_error,
)


class Plan:
    def __init__(self, operator_type, children=None):
        self.operator_type = operator_type
        self.children = children or []


def test_plan_operator_searches_nested_driver_plan_objects():
    plan = Plan("ProduceResults", [Plan("NodeCountFromCountStore")])

    assert _plan_has_operator(plan, "NodeCountFromCountStore")


def test_plan_operator_accepts_operator_suffixes():
    plan = {"operatorType": "NodeCountFromCountStore@neo4j", "children": []}

    assert _plan_has_operator(plan, "NodeCountFromCountStore")


def test_plan_operator_returns_false_when_absent():
    plan = {"operator_type": "AllNodesScan", "children": [{"operator_type": "EagerAggregation"}]}

    assert not _plan_has_operator(plan, "NodeCountFromCountStore")


def test_debug_trace_json_shape_matches_spec():
    trace = DebugTrace(
        profile="local",
        target=RunTarget(
            database="neo4j",
            server_version="5.18.0",
            edition="enterprise",
            fingerprint="abc123",
            capabilities=Capabilities(apoc=False, count_store=True),
        ),
        visibility=Visibility(can_connect=True, can_read=True, can_show_procedures=True),
        counts=Counts(nodes=7, relationships=11),
    )

    payload = trace.as_json()

    assert payload == {
        "ok": True,
        "profile": "local",
        "target": {
            "database": "neo4j",
            "server_version": "5.18.0",
            "edition": "enterprise",
            "fingerprint": "abc123",
            "capabilities": {"apoc": False, "count_store": True},
        },
        "visibility": {
            "can_connect": True,
            "can_read": True,
            "can_show_procedures": True,
        },
        "counts": {"nodes": 7, "relationships": 11},
    }


def test_error_json_shape_matches_spec():
    error = CheckError(code="neo4j.auth_failed", message="bad credentials", fix="edit profiles.yml")

    assert error_json("local", error) == {
        "ok": False,
        "profile": "local",
        "error": {
            "code": "neo4j.auth_failed",
            "message": "bad credentials",
            "fix": "edit profiles.yml",
        },
    }


def test_fingerprint_is_stable_and_short():
    assert _fingerprint("bolt://x", "neo4j", "5") == _fingerprint("bolt://x", "neo4j", "5")
    assert len(_fingerprint("bolt://x", "neo4j", "5")) == 16


def test_count_store_probe_returns_false_when_explain_fails():
    client = object.__new__(Neo4jClient)

    def fail_explain(query):
        raise GraphCheckError("neo4j.query_failed", "bad", "fix")

    client.explain_read = fail_explain

    assert client._count_store_usable() is False


def test_count_store_probe_uses_explain_plan():
    client = object.__new__(Neo4jClient)
    client.explain_read = lambda query: Plan("NodeCountFromCountStore")

    assert client._count_store_usable() is True


def test_server_info_errors_when_metadata_missing():
    client = object.__new__(Neo4jClient)
    client.run_read = lambda query: []

    with pytest.raises(GraphCheckError) as caught:
        client._server_info()

    assert caught.value.error.code == "neo4j.query_failed"


def test_counts_are_converted_to_ints():
    client = object.__new__(Neo4jClient)
    rows = {
        "MATCH (n) RETURN count(n) AS count": [{"count": "3"}],
        "MATCH ()-[r]->() RETURN count(r) AS count": [{"count": "4"}],
    }
    client.run_read = lambda query: rows[query]

    assert client._counts() == Counts(nodes=3, relationships=4)


def test_apoc_probe_falls_back_to_show_procedures_when_version_is_missing():
    client = object.__new__(Neo4jClient)

    def run_read(query):
        if query == "CALL apoc.version() YIELD version RETURN version":
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j query failed: no procedure with the name apoc.version is registered",
                "fix",
            )
        return [{"count": 3}]

    client.run_read = run_read

    assert client._apoc_usable() is True


def test_apoc_probe_returns_false_when_no_apoc_procedures_are_visible():
    client = object.__new__(Neo4jClient)

    def run_read(query):
        if query == "CALL apoc.version() YIELD version RETURN version":
            raise GraphCheckError(
                "neo4j.query_failed",
                "Neo4j query failed: no procedure with the name apoc.version is registered",
                "fix",
            )
        return [{"count": 0}]

    client.run_read = run_read

    assert client._apoc_usable() is False


def test_probe_handles_permission_denied_apoc_probe():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
    )
    client.verify = lambda: None
    client._server_info = lambda: ("5.18.0", "enterprise")
    client._counts = lambda: Counts(nodes=1, relationships=2)
    client._count_store_usable = lambda: True

    def apoc_denied():
        raise GraphCheckError("neo4j.permission_denied", "denied", "fix")

    client._apoc_usable = apoc_denied

    target, visibility, counts = client.probe()

    assert target.capabilities.apoc is False
    assert target.capabilities.count_store is True
    assert visibility.can_show_procedures is False
    assert counts == Counts(nodes=1, relationships=2)


def test_probe_treats_missing_apoc_as_absent_capability():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
    )
    client.verify = lambda: None
    client._server_info = lambda: ("5.18.0", "enterprise")
    client._counts = lambda: Counts(nodes=1, relationships=2)
    client._count_store_usable = lambda: True

    def missing_apoc():
        raise GraphCheckError(
            "neo4j.query_failed",
            "Neo4j query failed: no procedure with the name apoc.version is registered",
            "fix",
        )

    client._apoc_usable = missing_apoc

    target, visibility, counts = client.probe()

    assert target.capabilities.apoc is False
    assert visibility.can_show_procedures is True
    assert counts == Counts(nodes=1, relationships=2)


def test_probe_reraises_unexpected_apoc_probe_error():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
    )
    client.verify = lambda: None
    client._server_info = lambda: ("5.18.0", "enterprise")

    def broken_apoc_probe():
        raise GraphCheckError("neo4j.query_failed", "Neo4j query failed: broken query", "fix")

    client._apoc_usable = broken_apoc_probe

    with pytest.raises(GraphCheckError) as caught:
        client.probe()

    assert caught.value.error.code == "neo4j.query_failed"


def test_apoc_absent_detection_is_specific_to_apoc_procedure_errors():
    assert _is_apoc_absent_error(
        GraphCheckError(
            "neo4j.query_failed",
            "Neo4j query failed: unknown procedure apoc.version",
            "fix",
        )
    )
    assert not _is_apoc_absent_error(
        GraphCheckError("neo4j.query_failed", "Neo4j query failed: broken query", "fix")
    )


def test_debug_trace_closes_client(monkeypatch):
    closed = False

    class FakeClient:
        def __init__(self, profile):
            self.profile = profile

        def probe(self):
            return (
                RunTarget(
                    database="neo4j",
                    server_version="5",
                    edition="community",
                    fingerprint="fp",
                    capabilities=Capabilities(apoc=False, count_store=False),
                ),
                Visibility(True, True, True),
                Counts(0, 0),
            )

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("graphcheck.neo4j_adapter.Neo4jClient", FakeClient)

    trace = debug_trace(
        "local",
        ConnectionProfile(
            uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
        ),
    )

    assert trace.profile == "local"
    assert closed is True


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (type("AuthError", (Exception,), {})("bad"), "neo4j.auth_failed"),
        (type("TokenExpired", (Exception,), {})("bad"), "neo4j.auth_failed"),
        (type("ServiceUnavailable", (Exception,), {})("down"), "neo4j.unreachable"),
        (type("SessionExpired", (Exception,), {})("down"), "neo4j.unreachable"),
        (Exception("database missingdb does not exist"), "neo4j.database_not_found"),
        (Exception("permission denied"), "neo4j.permission_denied"),
        (Exception("unexpected"), "neo4j.query_failed"),
    ],
)
def test_map_neo4j_error_codes(exc, code):
    assert map_neo4j_error(exc).error.code == code
