from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from graphcheck.engine.executor import execute_query
from graphcheck.engine.identifiers import node_pattern, property_access
from graphcheck.errors import GraphCheckError


class ParameterTokenResolver(Protocol):
    def resolve(self, token: str, client: object, *, timeout_s: float | None) -> object: ...


class GraphTokenResolver:
    """Resolve the graph-relative tokens frozen by SPEC-02."""

    def resolve(self, token: str, client: object, *, timeout_s: float | None) -> object:
        if token != "$first-active-customer":
            raise GraphCheckError(
                "engine.parameter_token_unknown",
                f"Unknown graph-relative parameter token {token!r}.",
                "Use a supported token or replace it with a pinned literal value.",
            )
        customer = node_pattern("n", "Customer")
        customer_id = property_access("n", "id")
        active = property_access("n", "active")
        status = property_access("n", "status")
        query = (
            f"MATCH {customer} "
            f"WHERE {customer_id} IS NOT NULL "
            f"AND coalesce({active} = true, "
            f"             toLower(toString({status})) = 'active', true) "
            f"RETURN {customer_id} AS value ORDER BY toString({customer_id}) LIMIT 1"
        )
        rows = execute_query(client, query, timeout_s=timeout_s).rows
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
