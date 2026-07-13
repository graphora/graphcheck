import pytest

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import (
    Counts,
    DebugTrace,
    Neo4jClient,
    QueryResult,
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


def test_fingerprint_is_canonical_and_changes_with_graph_structure_or_counts():
    counts = Counts(nodes=3, relationships=2)
    first = _fingerprint(("Customer", "Account"), ("OWNS",), counts)

    assert first == _fingerprint(("Account", "Customer"), ("OWNS",), counts)
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    assert first != _fingerprint(("Account",), ("OWNS",), counts)
    assert first != _fingerprint(
        ("Customer", "Account"),
        ("OWNS",),
        Counts(nodes=4, relationships=2),
    )


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


def test_count_probe_recomputes_remaining_timeout_between_queries(monkeypatch):
    client = object.__new__(Neo4jClient)
    captured = []
    rows = {
        "MATCH (n) RETURN count(n) AS count": [{"count": 3}],
        "MATCH ()-[r]->() RETURN count(r) AS count": [{"count": 4}],
    }

    def run_read(query, *, timeout_s):
        captured.append(timeout_s)
        return rows[query]

    ticks = iter([0.0, 1.0, 2.5])
    monkeypatch.setattr("graphcheck.neo4j_adapter.time.monotonic", lambda: next(ticks))
    client.run_read = run_read

    assert client._counts(timeout_s=10.0) == Counts(nodes=3, relationships=4)
    assert captured == [pytest.approx(9.0), pytest.approx(7.5)]


def test_schema_tokens_are_canonicalized_for_fingerprinting():
    client = object.__new__(Neo4jClient)
    client.run_read = lambda query: [
        {
            "labels": ["Customer", "Account", "Customer"],
            "relationship_types": ["OWNS", "CONTROLS"],
        }
    ]

    assert client._schema_tokens() == (
        ("Account", "Customer"),
        ("CONTROLS", "OWNS"),
    )


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
    client._schema_tokens = lambda: (("Customer",), ("OWNS",))
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
    client._schema_tokens = lambda: (("Customer",), ("OWNS",))
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


def test_transaction_timeout_error_has_an_actionable_timeout_fix():
    error_type = type(
        "ClientError",
        (Exception,),
        {"code": "Neo.TransientError.Transaction.TransactionTimedOut"},
    )

    mapped = map_neo4j_error(error_type("The transaction timed out"))

    assert mapped.error.code == "neo4j.query_failed"
    assert "timed out" in mapped.error.message
    assert "sampling" in mapped.error.fix


def test_run_read_uses_read_access_mode(monkeypatch):
    # Unit-level guard so a regression that drops READ_ACCESS fails fast CI, not only the
    # gated integration job. Read-only enforcement is the #1 accuracy-contract invariant.
    import neo4j

    captured: dict = {}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            return iter([])

    class _FakeDriver:
        def session(self, **kwargs):
            captured.update(kwargs)
            return _FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _FakeDriver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    assert client.run_read("RETURN 1") == []
    assert captured["default_access_mode"] == neo4j.READ_ACCESS
    assert captured["database"] == "neo4j"


def test_run_read_result_preserves_graph_values_columns_and_notifications(monkeypatch):
    import neo4j
    from neo4j.graph import Graph, Node

    node = Node(Graph(), "4:customer:1", 1, ["Customer"], {"customer_id": "C-1"})
    notification = {
        "code": "Neo.ClientNotification.Statement.CartesianProduct",
        "title": "Cartesian product",
        "description": "The query builds a cartesian product.",
    }

    class _FakeSummary:
        notifications = [notification]

    class _FakeResult:
        def keys(self):
            return ("customer", "count")

        def __iter__(self):
            return iter([neo4j.Record([("customer", node), ("count", 1)])])

        def consume(self):
            return _FakeSummary()

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            return _FakeResult()

    class _FakeDriver:
        def session(self, **kwargs):
            return _FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _FakeDriver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    rich = client.run_read_result("RETURN customer, count")

    assert isinstance(rich, QueryResult)
    assert rich.columns == ("customer", "count")
    assert rich.rows[0]["customer"] is node
    assert rich.rows[0]["customer"].element_id == "4:customer:1"
    assert rich.notifications == (notification,)
    # The frozen API remains intentionally plain/lossy for compatibility.
    assert client.run_read("RETURN customer, count") == [
        {"customer": {"customer_id": "C-1"}, "count": 1}
    ]


def test_run_read_result_uses_driver_query_timeout(monkeypatch):
    import neo4j

    captured: dict = {}

    class _FakeResult:
        def keys(self):
            return ("n",)

        def __iter__(self):
            return iter([])

        def consume(self):
            return None

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            captured["query"] = query
            captured["params"] = params
            return _FakeResult()

    class _FakeDriver:
        def session(self, **kwargs):
            captured.update(kwargs)
            return _FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _FakeDriver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    result = client.run_read_result("RETURN $n AS n", {"n": 1}, timeout_s=2.5)

    assert result.columns == ("n",)
    assert isinstance(captured["query"], neo4j.Query)
    assert captured["query"].text == "RETURN $n AS n"
    assert captured["query"].timeout == 2.5
    assert captured["params"] == {"n": 1}
    assert captured["default_access_mode"] == neo4j.READ_ACCESS


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "notifications": [
                {
                    "code": "Neo.ClientNotification.Statement.UnknownLabelWarning",
                    "title": "The provided label is not in the database.",
                    "description": "The missing label name is: CustomerTypo",
                }
            ]
        },
        {
            "statuses": [
                {
                    "neo4j_code": "Neo.ClientNotification.Statement.UnknownLabelWarning",
                    "title": "The provided label is not in the database.",
                    "description": "The missing label name is: CustomerTypo",
                    "diagnostic_record": {
                        "_severity": "WARNING",
                        "_classification": "UNRECOGNIZED",
                    },
                }
            ]
        },
        {
            "notifications": [],
            "statuses": [
                {
                    "neo4j_code": "Neo.ClientNotification.Statement.UnknownLabelWarning",
                    "title": "The provided label is not in the database.",
                    "description": "The missing label name is: CustomerTypo",
                }
            ],
        },
    ],
)
def test_run_read_result_turns_missing_label_notification_into_error(monkeypatch, metadata):
    import neo4j

    class _FakeSummary:
        def __init__(self):
            self.metadata = metadata

    class _FakeResult:
        def keys(self):
            return ("n",)

        def __iter__(self):
            return iter([])

        def consume(self):
            return _FakeSummary()

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            return _FakeResult()

    class _FakeDriver:
        def session(self, **kwargs):
            return _FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _FakeDriver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    with pytest.raises(GraphCheckError) as caught:
        client.run_read_result("MATCH (n:CustomerTypo) RETURN n")

    assert caught.value.error.code == "neo4j.query_failed"
    assert "label" in caught.value.error.message.lower()
    assert "CustomerTypo" in caught.value.error.message
    assert caught.value.error.fix.startswith("Correct the label")


def test_unknown_relationship_type_notification_is_a_query_error():
    from graphcheck.neo4j_adapter import _raise_for_missing_schema_reference

    with pytest.raises(GraphCheckError) as caught:
        _raise_for_missing_schema_reference(
            (
                {
                    "code": "Neo.ClientNotification.Statement.UnknownRelationshipTypeWarning",
                    "description": "The relationship type is not in the database: OWNZ",
                },
            )
        )

    assert caught.value.error.code == "neo4j.query_failed"
    assert "relationship type" in caught.value.error.message
    assert "OWNZ" in caught.value.error.message
