import threading
import time

import pytest

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.neo4j_adapter import (
    Counts,
    DebugTrace,
    Neo4jClient,
    QueryResult,
    ResultPolicy,
    Visibility,
    _fingerprint,
    _is_apoc_absent_error,
    _plan_has_operator,
    _ReadClassificationCache,
    debug_trace,
    error_json,
    init_trace,
    map_neo4j_error,
)


class Plan:
    def __init__(self, operator_type, children=None):
        self.operator_type = operator_type
        self.children = children or []


class _ReadPlanSummary:
    query_type = "r"


class _ReadPlanResult:
    def consume(self):
        return _ReadPlanSummary()


def _is_explain(query):
    return str(getattr(query, "text", query)).startswith("EXPLAIN ")


def test_driver_pool_and_timeouts_match_workload_concurrency(monkeypatch):
    captured = {}
    driver = object()

    def build(uri, **kwargs):
        captured.update(uri=uri, **kwargs)
        return driver

    monkeypatch.setattr("graphcheck.neo4j_adapter.GraphDatabase.driver", build)
    client = Neo4jClient(
        ConnectionProfile(
            uri="bolt://example",
            user="neo4j",
            password="secret",
            database="neo4j",
        ),
        max_concurrency=4,
    )

    assert client._driver is driver
    assert captured["max_connection_pool_size"] == 4
    assert captured["connection_timeout"] == 10.0
    assert captured["connection_acquisition_timeout"] == 10.0
    assert captured["fetch_size"] == 1000
    assert captured["max_transaction_retry_time"] == 0.0


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5])
def test_driver_rejects_invalid_workload_concurrency(invalid):
    with pytest.raises(ValueError, match="max_concurrency"):
        Neo4jClient(
            ConnectionProfile(
                uri="bolt://example",
                user="neo4j",
                password="secret",
                database="neo4j",
            ),
            max_concurrency=invalid,
        )


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5])
def test_driver_rejects_invalid_read_guard_cache_capacity(invalid):
    with pytest.raises(ValueError, match="read_guard_cache_capacity"):
        Neo4jClient(
            ConnectionProfile(
                uri="bolt://example",
                user="neo4j",
                password="secret",
                database="neo4j",
            ),
            read_guard_cache_capacity=invalid,
        )


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
        "blocked_checks": [],
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


def test_enterprise_privilege_probe_recomputes_timeout_for_home_database(monkeypatch):
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
    )
    captured = []
    privileges = [
        {
            "access": "GRANTED",
            "action": "MATCH",
            "graph": "HOME",
            "resource": "ALL_PROPERTIES",
            "segment": "NODE(*)",
        },
        {
            "access": "GRANTED",
            "action": "MATCH",
            "graph": "HOME",
            "resource": "ALL_PROPERTIES",
            "segment": "RELATIONSHIP(*)",
        },
    ]

    def run_read(query, *, timeout_s):
        captured.append(timeout_s)
        if query.startswith("SHOW USER PRIVILEGES"):
            return privileges
        if query == "SHOW HOME DATABASE":
            return [{"name": "neo4j", "aliases": []}]
        pytest.fail(f"unexpected query: {query}")

    ticks = iter([0.0, 1.0, 2.5])
    monkeypatch.setattr("graphcheck.neo4j_adapter.time.monotonic", lambda: next(ticks))
    client.run_read = run_read

    assert client._can_read("enterprise", timeout_s=10.0) is True
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


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [
                {
                    "access": "GRANTED",
                    "action": "match",
                    "graph": "*",
                    "resource": "all_properties",
                    "segment": "NODE(*)",
                },
                {
                    "access": "GRANTED",
                    "action": "match",
                    "graph": "*",
                    "resource": "all_properties",
                    "segment": "RELATIONSHIP(*)",
                },
            ],
            True,
        ),
        (
            [
                {
                    "access": "GRANTED",
                    "action": "traverse",
                    "graph": "neo4j",
                    "resource": "graph",
                    "segment": "NODE(*)",
                },
                {
                    "access": "GRANTED",
                    "action": "read",
                    "graph": "neo4j",
                    "resource": "all_properties",
                    "segment": "NODE(*)",
                },
                {
                    "access": "GRANTED",
                    "action": "traverse",
                    "graph": "neo4j",
                    "resource": "graph",
                    "segment": "RELATIONSHIP(*)",
                },
                {
                    "access": "GRANTED",
                    "action": "read",
                    "graph": "neo4j",
                    "resource": "all_properties",
                    "segment": "RELATIONSHIP(*)",
                },
            ],
            True,
        ),
        ([{"access": "GRANTED", "action": "access", "graph": "neo4j"}], False),
    ],
)
def test_enterprise_read_probe_uses_current_user_graph_privileges(rows, expected):
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
    )
    client.run_read = lambda query: rows

    assert client._can_read("enterprise") is expected


def test_enterprise_read_probe_rejects_label_and_property_scoped_grant():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="restricted", password="pw", database="neo4j"
    )
    client.run_read = lambda query: [
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "neo4j",
            "resource": "property(name)",
            "segment": "NODE(Customer)",
        },
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "neo4j",
            "resource": "all_properties",
            "segment": "RELATIONSHIP(*)",
        },
    ]

    assert client._can_read("enterprise") is False


def test_enterprise_read_probe_rejects_scoped_denial():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="restricted", password="pw", database="neo4j"
    )
    client.run_read = lambda query: [
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "NODE(*)",
        },
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "RELATIONSHIP(*)",
        },
        {
            "access": "DENIED",
            "action": "read",
            "graph": "neo4j",
            "resource": "property(ssn)",
            "segment": "NODE(Customer)",
        },
    ]

    assert client._can_read("enterprise") is False


def test_enterprise_read_probe_resolves_full_home_graph_grant():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="home_reader", password="pw", database="neo4j"
    )
    privileges = [
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "HOME",
            "resource": "all_properties",
            "segment": "NODE(*)",
        },
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "HOME",
            "resource": "all_properties",
            "segment": "RELATIONSHIP(*)",
        },
    ]

    def run_read(query):
        if query.startswith("SHOW USER PRIVILEGES"):
            return privileges
        if query.startswith("SHOW HOME DATABASE"):
            return [{"name": "neo4j", "aliases": []}]
        pytest.fail(f"unexpected query: {query}")

    client.run_read = run_read

    assert client._can_read("enterprise") is True


def test_enterprise_read_probe_applies_scoped_home_graph_denial():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="home_denied", password="pw", database="neo4j"
    )
    privileges = [
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "NODE(*)",
        },
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "RELATIONSHIP(*)",
        },
        {
            "access": "DENIED",
            "action": "read",
            "graph": "HOME",
            "resource": "property(ssn)",
            "segment": "NODE(Customer)",
        },
    ]

    def run_read(query):
        if query.startswith("SHOW USER PRIVILEGES"):
            return privileges
        if query.startswith("SHOW HOME DATABASE"):
            return [{"name": "neo4j", "aliases": []}]
        pytest.fail(f"unexpected query: {query}")

    client.run_read = run_read

    assert client._can_read("enterprise") is False


def test_enterprise_read_probe_ignores_home_denial_for_non_home_database():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="home_denied", password="pw", database="analytics"
    )
    privileges = [
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "NODE(*)",
        },
        {
            "access": "GRANTED",
            "action": "match",
            "graph": "*",
            "resource": "all_properties",
            "segment": "RELATIONSHIP(*)",
        },
        {
            "access": "DENIED",
            "action": "read",
            "graph": "HOME",
            "resource": "property(ssn)",
            "segment": "NODE(Customer)",
        },
    ]

    def run_read(query):
        if query.startswith("SHOW USER PRIVILEGES"):
            return privileges
        if query.startswith("SHOW HOME DATABASE"):
            return [{"name": "neo4j", "aliases": []}]
        pytest.fail(f"unexpected query: {query}")

    client.run_read = run_read

    assert client._can_read("enterprise") is True


def test_community_read_probe_uses_implied_admin_privileges():
    client = object.__new__(Neo4jClient)
    client.run_read = lambda query: pytest.fail("Community probe should not inspect RBAC")

    assert client._can_read("community") is True


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
    client._can_read = lambda edition: True
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
    client._can_read = lambda edition: True
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


def test_probe_skips_counts_when_read_privilege_is_missing():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="restricted", password="pw", database="neo4j"
    )
    client.verify = lambda: None
    client._server_info = lambda: ("5.18.0", "enterprise")
    client._apoc_usable = lambda: False
    client._can_read = lambda edition: False
    client._counts = lambda: pytest.fail("counts must not run without read visibility")
    client._count_store_usable = lambda: pytest.fail(
        "count-store probe must not run without read visibility"
    )

    target, visibility, counts = client.probe()

    assert visibility.can_read is False
    assert target.capabilities.count_store is False
    assert counts == Counts(nodes=None, relationships=None)


def test_probe_handles_permission_denied_while_loading_counts():
    client = object.__new__(Neo4jClient)
    client._profile = ConnectionProfile(
        uri="bolt://localhost:7687", user="restricted", password="pw", database="neo4j"
    )
    client.verify = lambda: None
    client._server_info = lambda: ("5.18.0", "enterprise")
    client._apoc_usable = lambda: False
    client._can_read = lambda edition: True
    client._count_store_usable = lambda: pytest.fail(
        "count-store probe must not run after count permission denial"
    )

    def counts_denied():
        raise GraphCheckError("neo4j.permission_denied", "denied", "fix")

    client._counts = counts_denied

    target, visibility, counts = client.probe()

    assert visibility.can_read is False
    assert target.capabilities.count_store is False
    assert counts == Counts(nodes=None, relationships=None)


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
                    capabilities=Capabilities(apoc=True, count_store=False),
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
    assert trace.target.capabilities.apoc is True
    assert closed is True


def test_init_trace_uses_apoc_probe(monkeypatch):
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
                    capabilities=Capabilities(apoc=True, count_store=False),
                ),
                Visibility(True, True, True),
                Counts(0, 0),
            )

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("graphcheck.neo4j_adapter.Neo4jClient", FakeClient)

    trace = init_trace(
        "local",
        ConnectionProfile(
            uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j"
        ),
    )

    assert trace.target.capabilities.apoc is True
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
    assert isinstance(mapped, GraphCheckTimeoutError)
    assert "timed out" in mapped.error.message
    assert "sampling" in mapped.error.fix


def test_map_neo4j_error_uses_driver_security_code_for_permission_denial():
    exc = Exception("operation rejected")
    exc.code = "Neo.ClientError.Security.Forbidden"

    assert map_neo4j_error(exc).error.code == "neo4j.permission_denied"


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
            if _is_explain(query):
                return _ReadPlanResult()
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


def test_read_transaction_runs_measurement_and_evidence_on_one_transaction(monkeypatch):
    import neo4j

    executed = []

    class _Summary:
        query_type = "r"
        notifications = []

    class _Result:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def __iter__(self):
            return iter(self.rows)

        def keys(self):
            return ["value"]

        def consume(self):
            return _Summary()

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            assert isinstance(query, str)
            text = str(getattr(query, "text", query))
            executed.append(text)
            return (
                _ReadPlanResult()
                if text.startswith("EXPLAIN ")
                else _Result([neo4j.Record([("value", len(executed))])])
            )

    transaction = _Transaction()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def begin_transaction(self, *, timeout):
            assert 0 < timeout <= 5
            return transaction

    class _Driver:
        def session(self, **kwargs):
            assert kwargs["default_access_mode"] == neo4j.READ_ACCESS
            return _Session()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _Driver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    with client.read_transaction(timeout_s=5) as reader:
        measurement = reader.run_read_result("RETURN 1 AS value /* measurement */")
        evidence = reader.run_read_result("RETURN 2 AS value /* evidence */")

    assert measurement.rows[0]["value"] == 2
    assert evidence.rows[0]["value"] == 4
    assert executed == [
        "EXPLAIN RETURN 1 AS value /* measurement */",
        "RETURN 1 AS value /* measurement */",
        "EXPLAIN RETURN 2 AS value /* evidence */",
        "RETURN 2 AS value /* evidence */",
    ]


def test_run_read_rejects_server_classified_write_before_execution(monkeypatch):
    import neo4j

    executed: list[str] = []

    class _WriteSummary:
        query_type = "w"

    class _WritePlanResult:
        def consume(self):
            return _WriteSummary()

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            text = str(getattr(query, "text", query))
            if text.startswith("EXPLAIN "):
                return _WritePlanResult()
            executed.append(text)
            return iter([])

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
        client.run_read_result("CREATE (:Forbidden)")

    assert caught.value.error.code == "neo4j.write_rejected"
    assert executed == []


def test_plain_read_compatibility_path_does_not_reclassify_trusted_dbms_procedure(monkeypatch):
    import neo4j

    queries: list[str] = []

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            text = str(getattr(query, "text", query))
            queries.append(text)
            if text.startswith("EXPLAIN "):
                raise AssertionError("trusted compatibility reads must not be planner-reclassified")
            return iter([neo4j.Record([("version", "5.18.0")])])

    class _FakeDriver:
        def session(self, **kwargs):
            return _FakeSession()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _FakeDriver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    rows = client.run_read("CALL dbms.components() YIELD versions RETURN versions[0] AS version")

    assert rows == [{"version": "5.18.0"}]
    assert queries == ["CALL dbms.components() YIELD versions RETURN versions[0] AS version"]


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
            if _is_explain(query):
                return _ReadPlanResult()
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


def test_bounded_result_stops_without_draining_and_closes_the_session(monkeypatch):
    import neo4j

    state = {
        "yielded": 0,
        "cancelled": False,
        "consumed": False,
        "session_closed": False,
        "exceptional_exit": False,
    }

    class _LazyResult:
        def keys(self):
            return ("value",)

        def __iter__(self):
            return self

        def __next__(self):
            state["yielded"] += 1
            if state["yielded"] > 100:
                raise StopIteration
            return {"value": state["yielded"]}

        def cancel(self):
            state["cancelled"] = True

        def consume(self):
            state["consumed"] = True
            raise AssertionError("a truncated result must not be drained")

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            state["session_closed"] = True
            state["exceptional_exit"] = exc[0] is not None
            return False

        def run(self, query, params):
            return _ReadPlanResult() if _is_explain(query) else _LazyResult()

    class _Driver:
        def session(self, **kwargs):
            return _Session()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _Driver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    result = client.run_read_result_bounded(
        "RETURN value /* bounded-lazy */",
        policy=ResultPolicy(max_rows=3),
    )

    assert result.rows == [{"value": 1}, {"value": 2}, {"value": 3}]
    assert (result.complete, result.observed_rows, result.limit) == (False, 4, 3)
    assert state == {
        "yielded": 4,
        "cancelled": True,
        "consumed": False,
        "session_closed": True,
        "exceptional_exit": True,
    }


def test_bounded_result_exactly_at_limit_is_complete(monkeypatch):
    import neo4j

    class _Result:
        def keys(self):
            return ("value",)

        def __iter__(self):
            return iter([{"value": 1}, {"value": 2}, {"value": 3}])

        def consume(self):
            return object()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, params):
            return _ReadPlanResult() if _is_explain(query) else _Result()

    class _Driver:
        def session(self, **kwargs):
            return _Session()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _Driver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    result = client.run_read_result_bounded(
        "RETURN value /* bounded-exact */",
        policy=ResultPolicy(max_rows=3, require_complete=True),
    )

    assert result.complete is True
    assert result.observed_rows == 3
    assert len(result.rows) == 3


def test_bounded_complete_policy_errors_when_safety_ceiling_is_exceeded(monkeypatch):
    import neo4j

    state = {"cancelled": False, "session_closed": False}

    class _Result:
        def keys(self):
            return ("value",)

        def __iter__(self):
            return iter([{"value": 1}, {"value": 2}])

        def cancel(self):
            state["cancelled"] = True

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            state["session_closed"] = True
            return False

        def run(self, query, params):
            return _ReadPlanResult() if _is_explain(query) else _Result()

    class _Driver:
        def session(self, **kwargs):
            return _Session()

        def close(self):
            pass

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _Driver())
    client = Neo4jClient(
        ConnectionProfile(uri="bolt://x", user="u", password="p", database="neo4j")
    )

    with pytest.raises(GraphCheckError) as caught:
        client.run_read_result_bounded(
            "RETURN value /* bounded-required */",
            policy=ResultPolicy(max_rows=1, require_complete=True),
        )

    assert caught.value.error.code == "engine.result_limit_exceeded"
    assert state == {"cancelled": True, "session_closed": True}


def test_run_read_result_uses_driver_query_timeout(monkeypatch):
    import neo4j

    captured: dict = {"queries": []}

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
            captured["queries"].append(query)
            if _is_explain(query):
                return _ReadPlanResult()
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
    assert len(captured["queries"]) == 2
    assert captured["queries"][0].text == "EXPLAIN RETURN $n AS n"
    assert isinstance(captured["query"], neo4j.Query)
    assert captured["query"].text == "RETURN $n AS n"
    assert 0 < captured["query"].timeout <= 2.5
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
            if _is_explain(query):
                return _ReadPlanResult()
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

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "label" in caught.value.error.message.lower()
    assert "CustomerTypo" in caught.value.error.message
    assert caught.value.error.fix.startswith("Correct the label")


def test_gql_status_object_reports_missing_schema_without_deprecated_notification_api(
    monkeypatch,
):
    import neo4j

    class _Status:
        is_notification = True
        gql_status = "01N50"
        status_description = "warn: the label is not in the database: CustomerTypo"
        raw_severity = "WARNING"
        raw_classification = "UNRECOGNIZED"
        position = None

    class _FakeSummary:
        gql_status_objects = (_Status(),)

        @property
        def notifications(self):
            raise AssertionError("deprecated notification API must not be accessed")

        @property
        def summary_notifications(self):
            raise AssertionError("deprecated notification API must not be accessed")

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
            if _is_explain(query):
                return _ReadPlanResult()
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

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "label" in caught.value.error.message.lower()


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

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "relationship type" in caught.value.error.message
    assert "OWNZ" in caught.value.error.message


def test_read_classification_cache_is_bounded_per_client_and_cleared_on_close(monkeypatch):
    import neo4j

    drivers = []

    class _ExecutionResult:
        def keys(self):
            return ("value",)

        def __iter__(self):
            return iter([{"value": 1}])

        def consume(self):
            return object()

    class _Session:
        def __init__(self, driver):
            self.driver = driver

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, statement, params):
            text = str(getattr(statement, "text", statement))
            self.driver.calls.append(text)
            return _ReadPlanResult() if text.startswith("EXPLAIN ") else _ExecutionResult()

    class _Driver:
        def __init__(self):
            self.calls = []
            self.closed = False
            drivers.append(self)

        def session(self, **kwargs):
            return _Session(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *args, **kwargs: _Driver())
    first = Neo4jClient(
        ConnectionProfile(uri="bolt://first", user="u", password="p", database="neo4j"),
        read_guard_cache_capacity=2,
    )
    second = Neo4jClient(
        ConnectionProfile(uri="bolt://second", user="other", password="p", database="neo4j"),
        read_guard_cache_capacity=2,
    )

    first.run_read_result("RETURN $value AS value", {"value": 1})
    first.run_read_result("RETURN $value AS value", {"value": 2})
    second.run_read_result("RETURN $value AS value", {"value": 3})
    first.run_read_result("RETURN 2 AS value")
    first.run_read_result("RETURN $value AS value", {"value": 4})
    first.run_read_result("RETURN 3 AS value")
    first.run_read_result("RETURN 2 AS value")

    assert drivers[0].calls.count("EXPLAIN RETURN $value AS value") == 1
    assert drivers[1].calls.count("EXPLAIN RETURN $value AS value") == 1
    assert drivers[0].calls.count("EXPLAIN RETURN 2 AS value") == 2
    assert first.run_read_result("RETURN 2 AS value").read_guard_cache_hit is True
    assert first.read_guard_cache_info == (
        type(first.read_guard_cache_info)(max_size=2, size=2, in_flight=0, hits=3, misses=4)
    )
    first.close()
    assert drivers[0].closed is True
    assert first.read_guard_cache_info.size == 0
    assert first.read_guard_cache_info.hits == first.read_guard_cache_info.misses == 0


@pytest.mark.parametrize(
    "query_type, code", [("w", "neo4j.write_rejected"), ("", "neo4j.read_guard_unavailable")]
)
def test_failed_and_unknown_read_classifications_are_never_cached(query_type, code):
    calls = 0

    class _Summary:
        pass

    _Summary.query_type = query_type

    class _Result:
        def consume(self):
            return _Summary()

    class _Session:
        def run(self, query, params):
            nonlocal calls
            calls += 1
            return _Result()

    cache = _ReadClassificationCache(2)
    for _ in range(2):
        with pytest.raises(GraphCheckError) as caught:
            cache.ensure_read(
                _Session(),
                "CREATE (:Forbidden)",
                {},
                database="neo4j",
                deadline=None,
                attach_timeout=True,
            )
        assert caught.value.error.code == code

    assert calls == 2
    assert (cache.info().size, cache.info().misses) == (0, 2)


def test_read_classification_single_flight_wakes_waiters_on_success():
    cache = _ReadClassificationCache(2)
    owner_started = threading.Event()
    release_owner = threading.Event()
    calls = 0
    errors = []

    class _Session:
        def run(self, query, params):
            nonlocal calls
            calls += 1
            owner_started.set()
            assert release_owner.wait(2)
            return _ReadPlanResult()

    def classify():
        try:
            cache.ensure_read(
                _Session(),
                "RETURN 1",
                {},
                database="neo4j",
                deadline=time.monotonic() + 2,
                attach_timeout=True,
            )
        except BaseException as exc:
            errors.append(exc)

    owner = threading.Thread(target=classify)
    waiter = threading.Thread(target=classify)
    owner.start()
    assert owner_started.wait(1)
    waiter.start()
    time.sleep(0.02)
    release_owner.set()
    owner.join(2)
    waiter.join(2)

    assert not owner.is_alive() and not waiter.is_alive()
    assert errors == []
    assert calls == 1
    assert (cache.info().hits, cache.info().misses) == (1, 1)


def test_read_classification_wait_respects_its_deadline():
    cache = _ReadClassificationCache(2)
    owner_started = threading.Event()
    release_owner = threading.Event()
    owner_error = []

    class _Session:
        def run(self, query, params):
            owner_started.set()
            assert release_owner.wait(2)
            return _ReadPlanResult()

    def own():
        try:
            cache.ensure_read(
                _Session(),
                "RETURN 1",
                {},
                database="neo4j",
                deadline=time.monotonic() + 2,
                attach_timeout=True,
            )
        except BaseException as exc:
            owner_error.append(exc)

    owner = threading.Thread(target=own)
    owner.start()
    assert owner_started.wait(1)
    with pytest.raises(GraphCheckError) as caught:
        cache.ensure_read(
            _Session(),
            "RETURN 1",
            {},
            database="neo4j",
            deadline=time.monotonic() + 0.01,
            attach_timeout=True,
        )
    release_owner.set()
    owner.join(2)

    assert caught.value.error.code == "engine.timeout"
    assert owner_error == []


def test_read_classification_owner_failure_wakes_a_waiter():
    cache = _ReadClassificationCache(2)
    owner_started = threading.Event()
    release_owner = threading.Event()
    calls = 0
    outcomes = []

    class _Summary:
        def __init__(self, query_type):
            self.query_type = query_type

    class _Result:
        def __init__(self, query_type):
            self.query_type = query_type

        def consume(self):
            return _Summary(self.query_type)

    class _Session:
        def run(self, query, params):
            nonlocal calls
            calls += 1
            if calls == 1:
                owner_started.set()
                assert release_owner.wait(2)
                return _Result("w")
            return _Result("r")

    def classify():
        try:
            cache.ensure_read(
                _Session(),
                "RETURN 1",
                {},
                database="neo4j",
                deadline=time.monotonic() + 2,
                attach_timeout=True,
            )
            outcomes.append("read")
        except GraphCheckError as exc:
            outcomes.append(exc.error.code)

    owner = threading.Thread(target=classify)
    waiter = threading.Thread(target=classify)
    owner.start()
    assert owner_started.wait(1)
    waiter.start()
    time.sleep(0.02)
    release_owner.set()
    owner.join(2)
    waiter.join(2)

    assert sorted(outcomes) == ["neo4j.write_rejected", "read"]
    assert calls == 2
    assert (cache.info().size, cache.info().in_flight) == (1, 0)


def test_unknown_property_key_notification_is_a_query_error():
    from graphcheck.neo4j_adapter import _raise_for_missing_schema_reference

    with pytest.raises(GraphCheckError) as caught:
        _raise_for_missing_schema_reference(
            (
                {
                    "code": "Neo.ClientNotification.Statement.UnknownPropertyKeyWarning",
                    "description": "The property key is not in the database: customer_emali",
                },
            )
        )

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "property key" in caught.value.error.message
    assert "customer_emali" in caught.value.error.message
