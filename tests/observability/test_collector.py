from types import SimpleNamespace

import pytest

from graphcheck.observability import collector
from graphcheck.observability.health import HealthResult


def test_collector_updates_success_metrics(monkeypatch):
    db_up = SimpleNamespace(set=lambda value: None)
    connector = SimpleNamespace(set=lambda value: None)
    duration = SimpleNamespace(observe=lambda value: None)
    failures = SimpleNamespace(inc=lambda: None)
    last_ok = SimpleNamespace(set=lambda value: None)

    monkeypatch.setattr(collector, "DATABASE_UP", db_up)
    monkeypatch.setattr(collector, "CONNECTOR_CONNECTED", connector)
    monkeypatch.setattr(collector, "HEALTHCHECK_DURATION_SECONDS", duration)
    monkeypatch.setattr(collector, "HEALTHCHECK_FAILURES_TOTAL", failures)
    monkeypatch.setattr(collector, "LAST_HEALTHCHECK_TIMESTAMP_SECONDS", last_ok)

    def fake_health_check(client):
        return HealthResult(
            database_up=True,
            connector_connected=True,
            duration_seconds=0.25,
            timestamp=100.0,
        )

    monkeypatch.setattr(collector, "check_database_health", fake_health_check)

    result = collector.collect_database_health(object())

    assert result.database_up is True
    assert result.connector_connected is True
    assert result.duration_seconds == 0.25
    db_up.set = pytest.MonkeyPatch()


def test_collector_updates_failure_metrics(monkeypatch):
    db_up = SimpleNamespace(set=lambda value: None)
    connector = SimpleNamespace(set=lambda value: None)
    duration = SimpleNamespace(observe=lambda value: None)
    calls = {"inc": 0}

    class FakeCounter:
        def inc(self):
            calls["inc"] += 1

    failures = FakeCounter()
    last_ok = SimpleNamespace(set=lambda value: None)

    monkeypatch.setattr(collector, "DATABASE_UP", db_up)
    monkeypatch.setattr(collector, "CONNECTOR_CONNECTED", connector)
    monkeypatch.setattr(collector, "HEALTHCHECK_DURATION_SECONDS", duration)
    monkeypatch.setattr(collector, "HEALTHCHECK_FAILURES_TOTAL", failures)
    monkeypatch.setattr(collector, "LAST_HEALTHCHECK_TIMESTAMP_SECONDS", last_ok)

    def fake_health_check(client):
        return HealthResult(
            database_up=False,
            connector_connected=False,
            duration_seconds=0.5,
            timestamp=200.0,
            error="database unavailable",
        )

    monkeypatch.setattr(collector, "check_database_health", fake_health_check)

    result = collector.collect_database_health(object())

    assert result.database_up is False
    assert result.connector_connected is False
    assert result.error == "database unavailable"
    assert calls["inc"] == 1


def test_failure_counter_increments_only_on_failures(monkeypatch):
    calls = {"success": 0, "failure": 0, "inc": 0}

    def fake_success(client):
        calls["success"] += 1
        return HealthResult(
            database_up=True,
            connector_connected=True,
            duration_seconds=0.25,
            timestamp=100.0,
        )

    def fake_failure(client):
        calls["failure"] += 1
        return HealthResult(
            database_up=False,
            connector_connected=False,
            duration_seconds=0.50,
            timestamp=200.0,
            error="database unavailable",
        )

    class FakeCounter:
        def inc(self):
            calls["inc"] += 1

    monkeypatch.setattr(collector, "DATABASE_UP", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(collector, "CONNECTOR_CONNECTED", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(
        collector, "HEALTHCHECK_DURATION_SECONDS", SimpleNamespace(observe=lambda value: None)
    )
    monkeypatch.setattr(
        collector, "LAST_HEALTHCHECK_TIMESTAMP_SECONDS", SimpleNamespace(set=lambda value: None)
    )
    monkeypatch.setattr(collector, "HEALTHCHECK_FAILURES_TOTAL", FakeCounter())

    monkeypatch.setattr(collector, "check_database_health", fake_success)
    collector.collect_database_health(object())
    assert calls["inc"] == 0

    monkeypatch.setattr(collector, "check_database_health", fake_failure)
    collector.collect_database_health(object())
    assert calls["inc"] == 1


def test_last_successful_timestamp_changes_only_after_success(monkeypatch):
    calls = {"set": []}

    def fake_success(client):
        return HealthResult(
            database_up=True,
            connector_connected=True,
            duration_seconds=0.1,
            timestamp=1234.0,
        )

    def fake_failure(client):
        return HealthResult(
            database_up=False,
            connector_connected=False,
            duration_seconds=0.2,
            timestamp=5678.0,
            error="database unavailable",
        )

    class FakeGauge:
        def set(self, value):
            calls["set"].append(value)

    monkeypatch.setattr(collector, "DATABASE_UP", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(collector, "CONNECTOR_CONNECTED", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(
        collector, "HEALTHCHECK_DURATION_SECONDS", SimpleNamespace(observe=lambda value: None)
    )
    monkeypatch.setattr(collector, "HEALTHCHECK_FAILURES_TOTAL", SimpleNamespace(inc=lambda: None))
    monkeypatch.setattr(collector, "LAST_HEALTHCHECK_TIMESTAMP_SECONDS", FakeGauge())

    monkeypatch.setattr(collector, "check_database_health", fake_success)
    collector.collect_database_health(object())
    assert calls["set"] == [1234.0]

    monkeypatch.setattr(collector, "check_database_health", fake_failure)
    collector.collect_database_health(object())
    assert calls["set"] == [1234.0]


def test_collector_delegates_to_health_check(monkeypatch):
    called = {"count": 0}

    class Client:
        pass

    client = Client()

    def fake_health_check(candidate):
        called["count"] += 1
        assert candidate is client
        return HealthResult(
            database_up=True,
            connector_connected=True,
            duration_seconds=0.3,
            timestamp=42.0,
        )

    monkeypatch.setattr(collector, "DATABASE_UP", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(collector, "CONNECTOR_CONNECTED", SimpleNamespace(set=lambda value: None))
    monkeypatch.setattr(
        collector, "HEALTHCHECK_DURATION_SECONDS", SimpleNamespace(observe=lambda value: None)
    )
    monkeypatch.setattr(collector, "HEALTHCHECK_FAILURES_TOTAL", SimpleNamespace(inc=lambda: None))
    monkeypatch.setattr(
        collector, "LAST_HEALTHCHECK_TIMESTAMP_SECONDS", SimpleNamespace(set=lambda value: None)
    )
    monkeypatch.setattr(collector, "check_database_health", fake_health_check)

    result = collector.collect_database_health(client)

    assert called["count"] == 1
    assert result == HealthResult(
        database_up=True,
        connector_connected=True,
        duration_seconds=0.3,
        timestamp=42.0,
    )
