import pytest

from graphcheck.observability import server


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, (server.DEFAULT_HOST, server.DEFAULT_PORT), id="defaults"),
        pytest.param({"host": "0.0.0.0", "port": 9200}, ("0.0.0.0", 9200), id="custom"),
    ],
)
def test_start_metrics_server_delegates_once(monkeypatch, kwargs, expected):
    calls = []
    monkeypatch.setattr(
        server.prometheus_client,
        "start_http_server",
        lambda port, *, addr: calls.append((addr, port)),
    )

    server.start_metrics_server(**kwargs)

    assert calls == [expected]
