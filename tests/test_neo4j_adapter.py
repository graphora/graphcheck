from graphcheck.contracts.results import Capabilities, CheckError, RunTarget
from graphcheck.neo4j_adapter import Counts, DebugTrace, Visibility, _plan_has_operator, error_json


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

