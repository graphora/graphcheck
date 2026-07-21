from dataclasses import dataclass

import pytest

from graphcheck.engine.parameters import GraphTokenResolver, resolve_parameters
from graphcheck.errors import GraphCheckError


@dataclass(frozen=True)
class RichRows:
    rows: list[dict[str, object]]


class RichClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def run_read_result(self, query, params, *, timeout_s=None):
        self.calls.append((query, params, timeout_s))
        return RichRows(self.rows)


class LegacyClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def run_read(self, query, params):
        self.calls.append((query, params))
        return self.rows


def test_graph_token_uses_rich_read_api_with_timeout_and_stable_selection():
    client = RichClient([{"value": "CUST-0001"}])

    value = GraphTokenResolver().resolve("$first-active-customer", client, timeout_s=4.5)

    assert value == "CUST-0001"
    assert len(client.calls) == 1
    query, params, timeout = client.calls[0]
    assert "MATCH (n)" in query
    assert "'Customer' IN labels(n)" in query
    assert "ORDER BY toString(n.id) LIMIT 1" in query
    assert params == {}
    assert timeout == 4.5


def test_graph_token_supports_legacy_spec_03_read_api():
    client = LegacyClient([{"value": 1042}])

    value = GraphTokenResolver().resolve("$first-active-customer", client, timeout_s=9.0)

    assert value == 1042
    assert len(client.calls) == 1
    assert client.calls[0][1] == {}


@pytest.mark.parametrize("rows", [[], [{"value": None}], [{}]])
def test_graph_token_reports_unresolved_when_graph_has_no_candidate(rows):
    with pytest.raises(GraphCheckError) as caught:
        GraphTokenResolver().resolve("$first-active-customer", RichClient(rows), timeout_s=1.0)

    assert caught.value.error.code == "engine.parameter_token_unresolved"
    assert "$first-active-customer" in caught.value.error.message
    assert caught.value.error.fix


@pytest.mark.parametrize("token", ["$unknown", "$first-customer", "$"])
def test_unknown_graph_token_is_a_loud_error_without_querying(token):
    client = RichClient([{"value": "must-not-be-used"}])

    with pytest.raises(GraphCheckError) as caught:
        GraphTokenResolver().resolve(token, client, timeout_s=1.0)

    assert caught.value.error.code == "engine.parameter_token_unknown"
    assert token in caught.value.error.message
    assert caught.value.error.fix
    assert client.calls == []


def test_graph_token_rejects_connector_without_read_api():
    with pytest.raises(GraphCheckError) as caught:
        GraphTokenResolver().resolve("$first-active-customer", object(), timeout_s=1.0)

    assert caught.value.error.code == "engine.connector_invalid"
    assert "run_read" in caught.value.error.message
    assert caught.value.error.fix


def test_resolve_parameters_only_resolves_leading_token_strings():
    calls = []

    class Resolver:
        def resolve(self, token, client, *, timeout_s):
            calls.append((token, client, timeout_s))
            return f"resolved:{token}"

    client = object()
    params = {
        "token": "$first-active-customer",
        "embedded": "prefix-$not-a-token",
        "integer": 7,
        "nested": {"value": "$not-recursive"},
    }

    resolved = resolve_parameters(params, client, resolver=Resolver(), timeout_s=2.25)

    assert resolved == {
        "token": "resolved:$first-active-customer",
        "embedded": "prefix-$not-a-token",
        "integer": 7,
        "nested": {"value": "$not-recursive"},
    }
    assert calls == [("$first-active-customer", client, 2.25)]
    assert resolved is not params


def test_multiple_parameter_tokens_resolve_in_mapping_order():
    calls = []

    class Resolver:
        def resolve(self, token, client, *, timeout_s):
            calls.append(token)
            return len(calls)

    resolved = resolve_parameters(
        {"first": "$one", "literal": "x", "second": "$two"},
        object(),
        resolver=Resolver(),
    )

    assert resolved == {"first": 1, "literal": "x", "second": 2}
    assert calls == ["$one", "$two"]


def test_repeated_tokens_are_cached_and_distinct_tokens_get_fresh_timeouts():
    calls = []
    timeouts = iter([9.0, 7.5])

    class Resolver:
        def resolve(self, token, client, *, timeout_s):
            calls.append((token, timeout_s))
            return f"resolved:{token}"

    resolved = resolve_parameters(
        {"first": "$same", "repeat": "$same", "other": "$other"},
        object(),
        resolver=Resolver(),
        timeout_factory=lambda: next(timeouts),
    )

    assert resolved == {
        "first": "resolved:$same",
        "repeat": "resolved:$same",
        "other": "resolved:$other",
    }
    assert calls == [("$same", 9.0), ("$other", 7.5)]


def test_default_resolver_surfaces_unknown_token_from_parameter_mapping():
    with pytest.raises(GraphCheckError) as caught:
        resolve_parameters({"customer_id": "$unsupported"}, RichClient([]))

    assert caught.value.error.code == "engine.parameter_token_unknown"


def test_parameter_resolver_error_is_not_silently_replaced_with_literal():
    class Resolver:
        def resolve(self, token, client, *, timeout_s):
            raise GraphCheckError("resolver.failed", "cannot resolve", "pin a value")

    with pytest.raises(GraphCheckError) as caught:
        resolve_parameters({"customer_id": "$dynamic"}, object(), resolver=Resolver())

    assert caught.value.error.code == "resolver.failed"
