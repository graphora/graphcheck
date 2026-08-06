from __future__ import annotations

import time
from dataclasses import dataclass

from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient

# Lightweight connectivity check.
# This intentionally avoids database-specific procedures so it
# can execute on any supported Neo4j deployment.
HEALTH_QUERY = "RETURN 1 AS healthy"
HEALTHCHECK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class HealthResult:
    database_up: bool
    connector_connected: bool
    duration_seconds: float
    timestamp: float
    error: str | None = None


def check_database_health(client: Neo4jClient) -> HealthResult:
    """Execute one lightweight read through the existing Neo4j connector."""

    started = time.monotonic()
    try:
        rows = client.run_read(HEALTH_QUERY, timeout_s=HEALTHCHECK_TIMEOUT_SECONDS)
        if rows != [{"healthy": 1}]:
            return HealthResult(
                database_up=False,
                connector_connected=True,
                duration_seconds=max(0.0, time.monotonic() - started),
                timestamp=time.time(),
                error="The database health query returned an unexpected result.",
            )
    except GraphCheckError as exc:
        return HealthResult(
            database_up=False,
            connector_connected=exc.error.code != "neo4j.unreachable",
            duration_seconds=max(0.0, time.monotonic() - started),
            timestamp=time.time(),
            error=str(exc),
        )
    except Exception as exc:
        return HealthResult(
            database_up=False,
            connector_connected=False,
            duration_seconds=max(0.0, time.monotonic() - started),
            timestamp=time.time(),
            error=str(exc),
        )

    return HealthResult(
        database_up=True,
        connector_connected=True,
        duration_seconds=max(0.0, time.monotonic() - started),
        timestamp=time.time(),
    )


__all__ = [
    "HEALTH_QUERY",
    "HEALTHCHECK_TIMEOUT_SECONDS",
    "HealthResult",
    "check_database_health",
]
