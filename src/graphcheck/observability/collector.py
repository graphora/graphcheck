from __future__ import annotations

from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.observability.health import HealthResult, check_database_health
from graphcheck.observability.metrics import (
    CONNECTOR_CONNECTED,
    DATABASE_UP,
    HEALTHCHECK_DURATION_SECONDS,
    HEALTHCHECK_FAILURES_TOTAL,
    LAST_HEALTHCHECK_TIMESTAMP_SECONDS,
)


def collect_database_health(client: Neo4jClient) -> HealthResult:
    """Run a database health check and publish the result into Prometheus metrics."""

    result = check_database_health(client)

    DATABASE_UP.set(1.0 if result.database_up else 0.0)
    CONNECTOR_CONNECTED.set(1.0 if result.connector_connected else 0.0)
    HEALTHCHECK_DURATION_SECONDS.observe(result.duration_seconds)

    if result.database_up:
        LAST_HEALTHCHECK_TIMESTAMP_SECONDS.set(result.timestamp)
    else:
        HEALTHCHECK_FAILURES_TOTAL.inc()

    return result


__all__ = ["collect_database_health"]
