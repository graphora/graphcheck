import pytest

from graphcheck.errors import GraphCheckError
from graphcheck.observability.health import (
    HEALTH_QUERY,
    HEALTHCHECK_TIMEOUT_SECONDS,
    HealthResult,
    check_database_health,
)


class HealthyClient:
    def run_read(self, query, *, timeout_s):
        assert query == HEALTH_QUERY
        assert timeout_s == HEALTHCHECK_TIMEOUT_SECONDS
        return [{"healthy": 1}]


class UnreachableClient:
    def run_read(self, query, *, timeout_s):
        assert query == HEALTH_QUERY
        assert timeout_s == HEALTHCHECK_TIMEOUT_SECONDS
        raise GraphCheckError("neo4j.unreachable", "database unavailable", "fix")


def test_health_check_returns_structured_success(monkeypatch):
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr("graphcheck.observability.health.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("graphcheck.observability.health.time.time", lambda: 1_754_352_000.0)

    result = check_database_health(HealthyClient())

    assert result == HealthResult(
        database_up=True,
        connector_connected=True,
        duration_seconds=0.25,
        timestamp=1_754_352_000.0,
    )


def test_health_check_returns_failure_instead_of_raising(monkeypatch):
    ticks = iter([10.0, 10.5])
    monkeypatch.setattr("graphcheck.observability.health.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("graphcheck.observability.health.time.time", lambda: 1_754_352_001.0)

    result = check_database_health(UnreachableClient())

    assert result == HealthResult(
        database_up=False,
        connector_connected=False,
        duration_seconds=0.5,
        timestamp=1_754_352_001.0,
        error="database unavailable",
    )


@pytest.mark.parametrize(
    "error_code",
    [
        "neo4j.permission_denied",
        "neo4j.database_not_found",
        "neo4j.query_failed",
    ],
)
def test_health_check_distinguishes_database_failures_from_connectivity_failures(error_code):
    class DatabaseFailureClient:
        def run_read(self, query, *, timeout_s):
            assert query == HEALTH_QUERY
            assert timeout_s == HEALTHCHECK_TIMEOUT_SECONDS
            raise GraphCheckError(error_code, "health query failed", "fix")

    result = check_database_health(DatabaseFailureClient())

    assert result.connector_connected is True
    assert result.database_up is False


@pytest.mark.parametrize("rows", [[], [{"healthy": 0}], [{"unexpected": 1}]])
def test_health_check_rejects_an_unexpected_query_result(rows):
    class UnexpectedClient:
        def run_read(self, query, *, timeout_s):
            assert query == HEALTH_QUERY
            assert timeout_s == HEALTHCHECK_TIMEOUT_SECONDS
            return rows

    result = check_database_health(UnexpectedClient())

    assert result.database_up is False
    assert result.connector_connected is True
    assert result.error == "The database health query returned an unexpected result."


def test_health_check_passes_connector_timeout():
    calls = []

    class RecordingClient:
        def run_read(self, query, *, timeout_s):
            calls.append((query, timeout_s))
            return [{"healthy": 1}]

    result = check_database_health(RecordingClient())

    assert result.database_up is True
    assert calls == [(HEALTH_QUERY, 5.0)]
