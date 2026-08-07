from __future__ import annotations

import time

from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.observability.collector import collect_database_health
from graphcheck.observability.server import DEFAULT_HOST, DEFAULT_PORT, start_metrics_server


def run_monitor(
    client: Neo4jClient,
    interval_seconds: int = 15,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Start the metrics server and repeatedly collect database health until interrupted."""

    start_metrics_server(host, port)

    try:
        while True:
            collect_database_health(client)
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        return


__all__ = ["run_monitor"]
