from dataclasses import dataclass

import pytest

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import compile_check
from graphcheck.engine.executor import Executor, ReadOnlyExecutor, execute_query
from graphcheck.errors import GraphCheckError


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
