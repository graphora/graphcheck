from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from graphcheck.engine.compiler import CompiledCheck
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import ResultPolicy


@dataclass(frozen=True)
class ExecutionResult:
    rows: list[dict[str, Any]]
    columns: tuple[str, ...]
    notification_count: int | None = None
    server_available_after_ms: int | None = None
    server_consumed_after_ms: int | None = None
    read_guard_ms: int | None = None
    read_guard_cache_hit: bool | None = None
    complete: bool = True
    observed_rows: int = 0
    limit: int | None = None


class ReadOnlyExecutor:
    """Execute compiled queries exclusively through C2's driver-enforced read path."""

    def __init__(self, client: object) -> None:
        self.client = client
        rich = getattr(client, "run_read_result", None)
        bounded = getattr(client, "run_read_result_bounded", None)
        legacy = getattr(client, "run_read", None)
        self._method = rich if callable(rich) else legacy if callable(legacy) else None
        self._bounded_method = bounded if callable(bounded) else None
        self._rich = callable(rich)
        parameters = _parameters(self._method) if self._method is not None else ()
        self._accepts_timeout = _parameters_accept(parameters, "timeout_s", variadic=True)
        self._accepts_missing_schema = _parameters_accept(parameters, "allow_missing_schema")

    def execute(
        self,
        compiled: CompiledCheck | str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_s: float | None = None,
        policy: ResultPolicy | None = None,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
        allow_missing_schema: bool = False,
    ) -> ExecutionResult:
        query = compiled.query if isinstance(compiled, CompiledCheck) else compiled
        values = dict(compiled.params if isinstance(compiled, CompiledCheck) else params or {})
        if self._method is None:
            raise GraphCheckError(
                "engine.connector_invalid",
                "The connector does not expose the SPEC-03 run_read method.",
                "Pass a configured Neo4jClient or another read-only C2 connector.",
            )
        kwargs = {"timeout_s": timeout_s} if self._accepts_timeout else {}
        if self._accepts_missing_schema:
            kwargs["allow_missing_schema"] = allow_missing_schema
        if self._rich:
            result = (
                self._bounded_method(
                    query,
                    values,
                    policy=policy,
                    stop_when=stop_when,
                    **kwargs,
                )
                if policy is not None and self._bounded_method is not None
                else self._method(query, values, **kwargs)
            )
            if hasattr(result, "rows"):
                rows = list(result.rows)
                complete = bool(getattr(result, "complete", True))
                observed_rows = int(getattr(result, "observed_rows", len(rows)))
                limit = getattr(result, "limit", None)
                if policy is not None and self._bounded_method is None:
                    rows, complete, observed_rows = _bound_eager_rows(
                        rows,
                        policy,
                        stop_when,
                    )
                    limit = policy.max_rows
                return ExecutionResult(
                    rows,
                    tuple(result.columns),
                    notification_count=len(getattr(result, "notifications", ())),
                    server_available_after_ms=getattr(result, "server_available_after_ms", None),
                    server_consumed_after_ms=getattr(result, "server_consumed_after_ms", None),
                    read_guard_ms=getattr(result, "read_guard_ms", None),
                    read_guard_cache_hit=getattr(result, "read_guard_cache_hit", None),
                    complete=complete,
                    observed_rows=observed_rows,
                    limit=limit,
                )
            rows = list(result)
            return ExecutionResult(rows, tuple(rows[0]) if rows else ())
        rows = list(self._method(query, values, **kwargs))
        if policy is not None:
            rows, complete, observed_rows = _bound_eager_rows(rows, policy, stop_when)
            return ExecutionResult(
                rows,
                tuple(rows[0]) if rows else (),
                complete=complete,
                observed_rows=observed_rows,
                limit=policy.max_rows,
            )
        return ExecutionResult(rows, tuple(rows[0]) if rows else ())

    @contextmanager
    def transaction(self, *, timeout_s: float | None = None, allow_missing_schema: bool = False):
        """Yield an executor bound to one connector read transaction when supported."""

        factory = getattr(self.client, "read_transaction", None)
        if not callable(factory):
            yield self
            return
        kwargs = {"timeout_s": timeout_s}
        if _accepts_parameter(factory, "allow_missing_schema"):
            kwargs["allow_missing_schema"] = allow_missing_schema
        with factory(**kwargs) as reader:
            yield ReadOnlyExecutor(reader)


Executor = ReadOnlyExecutor


def execute_query(
    client: object,
    compiled: CompiledCheck | str,
    params: Mapping[str, object] | None = None,
    *,
    timeout_s: float | None = None,
    policy: ResultPolicy | None = None,
    stop_when: Callable[[dict[str, Any]], bool] | None = None,
) -> ExecutionResult:
    return ReadOnlyExecutor(client).execute(
        compiled,
        params,
        timeout_s=timeout_s,
        policy=policy,
        stop_when=stop_when,
    )


def _accepts_timeout(method: Callable[..., object]) -> bool:
    return _parameters_accept(_parameters(method), "timeout_s", variadic=True)


def _accepts_parameter(method: Callable[..., object], name: str, *, variadic: bool = False) -> bool:
    return _parameters_accept(_parameters(method), name, variadic=variadic)


def _parameters(method: Callable[..., object]):
    try:
        return tuple(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        return ()


def _parameters_accept(parameters, name: str, *, variadic: bool = False) -> bool:
    return any(
        parameter.name == name or (variadic and parameter.kind is inspect.Parameter.VAR_KEYWORD)
        for parameter in parameters
    )


def _bound_eager_rows(
    rows: list[dict[str, Any]],
    policy: ResultPolicy,
    stop_when: Callable[[dict[str, Any]], bool] | None,
) -> tuple[list[dict[str, Any]], bool, int]:
    retained: list[dict[str, Any]] = []
    for observed, row in enumerate(rows, start=1):
        if policy.max_rows is not None and len(retained) >= policy.max_rows:
            if policy.require_complete:
                raise GraphCheckError(
                    "engine.result_limit_exceeded",
                    "The query result exceeded the configured safety ceiling "
                    f"of {policy.max_rows} rows.",
                    "Narrow the query or increase engine.result_row_limit after "
                    "reviewing its memory cost.",
                )
            return retained, False, observed
        retained.append(row)
        if stop_when is not None and stop_when(row):
            return retained, False, observed
    return retained, True, len(retained)
