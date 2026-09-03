import inspect
from dataclasses import dataclass

import pytest

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import compile_check
from graphcheck.engine.executor import Executor, ReadOnlyExecutor, execute_query
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import QueryResult, ResultPolicy


@dataclass(frozen=True)
class RichResult:
    rows: list[dict[str, object]]
    columns: tuple[str, ...]


def _compiled():
    suite = load_suite(
        """
suite: executor
competency:
  - id: query
    question: Does it execute?
    query: RETURN $value AS value
    params: {value: 7}
    expect: {rows: {exactly: 1}}
"""
    )
    return compile_check(suite.checks[0])


def test_executor_prefers_rich_c2_path_and_preserves_columns_and_timeout():
    calls = []

    class Client:
        def run_read_result(self, query, params, *, timeout_s=None):
            calls.append((query, params, timeout_s))
            return RichResult([{"value": 7}], ("value",))

        def run_read(self, query, params):
            raise AssertionError("legacy path must not run")

    compiled = _compiled()
    result = ReadOnlyExecutor(Client()).execute(compiled, timeout_s=3.5)

    assert result.rows == [{"value": 7}]
    assert result.columns == ("value",)
    assert calls == [(compiled.query, {"value": 7}, 3.5)]


def test_executor_forwards_explicit_missing_schema_allowance_only_when_supported():
    calls = []

    class Client:
        def run_read_result(self, query, params, *, timeout_s=None, allow_missing_schema=False):
            calls.append(allow_missing_schema)
            return RichResult([{"value": 7}], ("value",))

    ReadOnlyExecutor(Client()).execute(_compiled(), allow_missing_schema=True)

    assert calls == [True]


def test_executor_supports_frozen_legacy_c2_api_without_timeout_keyword():
    calls = []

    class Client:
        def run_read(self, query, params):
            calls.append((query, params))
            return [{"value": 9}]

    result = execute_query(Client(), "RETURN $value AS value", {"value": 9}, timeout_s=2)

    assert result.rows == [{"value": 9}]
    assert result.columns == ("value",)
    assert calls == [("RETURN $value AS value", {"value": 9})]


def test_executor_alias_is_the_read_only_executor():
    assert Executor is ReadOnlyExecutor


def test_executor_rejects_connector_without_read_api():
    with pytest.raises(GraphCheckError) as caught:
        ReadOnlyExecutor(object()).execute("RETURN 1")

    assert caught.value.error.code == "engine.connector_invalid"


def test_executor_inspects_timeout_support_only_during_construction(monkeypatch):
    calls = 0
    real_signature = inspect.signature

    def count_signature(method):
        nonlocal calls
        calls += 1
        return real_signature(method)

    class Client:
        def run_read(self, query, params, *, timeout_s=None):
            return [{"value": 1}]

    monkeypatch.setattr("graphcheck.engine.executor.inspect.signature", count_signature)
    executor = ReadOnlyExecutor(Client())

    executor.execute("RETURN 1 AS value")
    executor.execute("RETURN 1 AS value")

    assert calls == 1


def test_executor_routes_bounded_policies_and_completeness_metadata():
    calls = []

    class Client:
        def run_read_result(self, query, params, *, timeout_s=None):
            raise AssertionError("bounded path must be preferred")

        def run_read_result_bounded(
            self,
            query,
            params,
            *,
            policy,
            timeout_s=None,
            stop_when=None,
        ):
            calls.append((query, params, policy, timeout_s, stop_when))
            return QueryResult(
                rows=[{"value": 1}],
                columns=("value",),
                notifications=(),
                complete=False,
                observed_rows=1,
                limit=4,
            )

    policy = ResultPolicy(max_rows=4)

    def stop_when(row):
        return row["value"] == 1

    result = ReadOnlyExecutor(Client()).execute(
        "RETURN 1 AS value",
        timeout_s=2.5,
        policy=policy,
        stop_when=stop_when,
    )

    assert (result.complete, result.observed_rows, result.limit) == (False, 1, 4)
    assert calls == [("RETURN 1 AS value", {}, policy, 2.5, stop_when)]
