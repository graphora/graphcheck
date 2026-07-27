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


class ReadOnlyExecutor:
    """Execute compiled queries exclusively through C2's driver-enforced read path."""

    def __init__(self, client: object) -> None:
        self.client = client
        rich = getattr(client, "run_read_result", None)
        legacy = getattr(client, "run_read", None)
        self._method = rich if callable(rich) else legacy if callable(legacy) else None
        self._rich = callable(rich)
        self._accepts_timeout = (
            _accepts_timeout(self._method) if self._method is not None else False
        )

    def execute(
        self,
        compiled: CompiledCheck | str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_s: float | None = None,
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
        if self._rich:
            result = self._method(query, values, **kwargs)
            if hasattr(result, "rows"):
                return ExecutionResult(list(result.rows), tuple(result.columns))
            rows = list(result)
            return ExecutionResult(rows, tuple(rows[0]) if rows else ())
        rows = list(self._method(query, values, **kwargs))
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
