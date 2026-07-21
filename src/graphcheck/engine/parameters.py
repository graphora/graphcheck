from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from graphcheck.errors import GraphCheckError


class ParameterTokenResolver(Protocol):
    def resolve(self, token: str, client: object, *, timeout_s: float | None) -> object: ...


@dataclass(frozen=True)
class GraphTokenResolver:
    """Resolve the graph-relative tokens frozen by SPEC-02."""

    def resolve(self, token: str, client: object, *, timeout_s: float | None) -> object:
        if token != "$first-active-customer":
            raise GraphCheckError(
                "engine.parameter_token_unknown",
                f"Unknown graph-relative parameter token {token!r}.",
                "Use a supported token or replace it with a pinned literal value.",
            )
        query = (
            "MATCH (n) "
            "WHERE 'Customer' IN labels(n) AND n.id IS NOT NULL "
            "AND coalesce(n.active = true, "
            "             toLower(toString(n.status)) = 'active', true) "
            "RETURN n.id AS value ORDER BY toString(n.id) LIMIT 1"
        )
        rows = _run_rows(client, query, timeout_s=timeout_s)
        if not rows or rows[0].get("value") is None:
            raise GraphCheckError(
                "engine.parameter_token_unresolved",
                "No active Customer with an `id` was found for $first-active-customer.",
                "Populate an active Customer or pin an explicit customer id in `params`.",
            )
        return rows[0]["value"]


def resolve_parameters(
    params: Mapping[str, object],
    client: object,
    *,
    resolver: ParameterTokenResolver | None = None,
    timeout_s: float | None = None,
    timeout_factory: Callable[[], float] | None = None,
) -> dict[str, object]:
    resolver = resolver or GraphTokenResolver()
    resolved: dict[str, object] = {}
    token_cache: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$"):
            if value not in token_cache:
                current_timeout = timeout_factory() if timeout_factory is not None else timeout_s
                token_cache[value] = resolver.resolve(
                    value,
                    client,
                    timeout_s=current_timeout,
                )
            resolved[key] = token_cache[value]
        else:
            resolved[key] = value
    return resolved


def _run_rows(client: object, query: str, *, timeout_s: float | None) -> list[dict]:
    rich = getattr(client, "run_read_result", None)
    if callable(rich):
        kwargs = {"timeout_s": timeout_s} if _accepts_timeout(rich) else {}
        result = rich(query, {}, **kwargs)
        return list(result.rows)
    run_read = getattr(client, "run_read", None)
    if not callable(run_read):
        raise GraphCheckError(
            "engine.connector_invalid",
            "The connector does not expose the SPEC-03 run_read method.",
            "Pass a configured Neo4jClient or another read-only C2-compatible connector.",
        )
    kwargs = {"timeout_s": timeout_s} if _accepts_timeout(run_read) else {}
    return list(run_read(query, {}, **kwargs))


def _accepts_timeout(method: object) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "timeout_s" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
