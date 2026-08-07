from prometheus_client import Counter, Gauge, Histogram

DATABASE_UP = Gauge(
    "graphcheck_database_up",
    "Whether the connected database is reachable (1 = up, 0 = down).",
)

CONNECTOR_CONNECTED = Gauge(
    "graphcheck_connector_connected",
    "Whether the GraphCheck connector is currently connected.",
)

HEALTHCHECK_DURATION_SECONDS = Histogram(
    "graphcheck_healthcheck_duration_seconds",
    "Time spent performing database health checks.",
)

HEALTHCHECK_FAILURES_TOTAL = Counter(
    "graphcheck_healthcheck_failures_total",
    "Total number of failed database health checks.",
)

LAST_HEALTHCHECK_TIMESTAMP_SECONDS = Gauge(
    "graphcheck_last_healthcheck_timestamp_seconds",
    "Unix timestamp of the last successful health check.",
)

__all__ = [
    "CONNECTOR_CONNECTED",
    "DATABASE_UP",
    "HEALTHCHECK_DURATION_SECONDS",
    "HEALTHCHECK_FAILURES_TOTAL",
    "LAST_HEALTHCHECK_TIMESTAMP_SECONDS",
]
