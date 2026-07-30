from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from graphcheck.engine.compiler import CompiledCheck
from graphcheck.errors import GraphCheckError


@dataclass(frozen=True)
class ExecutionResult:
    rows: list[dict[str, Any]]
    columns: tuple[str, ...]
    notification_count: int | None = None
    server_available_after_ms: int | None = None
    server_consumed_after_ms: int | None = None
    read_guard_ms: int | None = None


class ReadOnlyExecutor:
    """Execute compiled queries exclusively through C2's driver-enforced read path."""

    def __init__(self, client: object) -> None:
        self.client = client

    def execute(
        self,
        compiled: CompiledCheck | str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> ExecutionResult:
        query = compiled.query if isinstance(compiled, CompiledCheck) else compiled
        values = dict(compiled.params if isinstance(compiled, CompiledCheck) else params or {})
        rich = getattr(self.client, "run_read_result", None)
        if callable(rich):
            kwargs = {"timeout_s": timeout_s} if _accepts_timeout(rich) else {}
            result = rich(query, values, **kwargs)
            if hasattr(result, "rows"):
                return ExecutionResult(
                    list(result.rows),
                    tuple(result.columns),
                    notification_count=len(getattr(result, "notifications", ())),
                    server_available_after_ms=getattr(result, "server_available_after_ms", None),
                    server_consumed_after_ms=getattr(result, "server_consumed_after_ms", None),
                    read_guard_ms=getattr(result, "read_guard_ms", None),
                )
            rows = list(result)
            return ExecutionResult(rows, tuple(rows[0]) if rows else ())

        run_read = getattr(self.client, "run_read", None)
        if not callable(run_read):
            raise GraphCheckError(
                "engine.connector_invalid",
                "The connector does not expose the SPEC-03 run_read method.",
                "Pass a configured Neo4jClient or another read-only C2 connector.",
            )
        kwargs = {"timeout_s": timeout_s} if _accepts_timeout(run_read) else {}
        rows = list(run_read(query, values, **kwargs))
        return ExecutionResult(rows, tuple(rows[0]) if rows else ())


Executor = ReadOnlyExecutor


def execute_query(
    client: object,
    compiled: CompiledCheck | str,
    params: Mapping[str, object] | None = None,
    *,
    timeout_s: float | None = None,
) -> ExecutionResult:
    return ReadOnlyExecutor(client).execute(compiled, params, timeout_s=timeout_s)


def _accepts_timeout(method: Callable[..., object]) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "timeout_s" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
