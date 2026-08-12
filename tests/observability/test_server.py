from graphcheck.observability import server


def test_start_metrics_server_uses_default_values(monkeypatch):
    calls = {}

    def fake_start_http_server(port, *, addr):
        calls["port"] = port
        calls["host"] = addr

    monkeypatch.setattr(server.prometheus_client, "start_http_server", fake_start_http_server)

    server.start_metrics_server()

    assert calls == {"host": server.DEFAULT_HOST, "port": server.DEFAULT_PORT}


def test_start_metrics_server_forwards_custom_values(monkeypatch):
    calls = {}

    def fake_start_http_server(port, *, addr):
        calls["port"] = port
        calls["host"] = addr

    monkeypatch.setattr(server.prometheus_client, "start_http_server", fake_start_http_server)

    server.start_metrics_server(host="127.0.0.1", port=9200)

    assert calls == {"host": "127.0.0.1", "port": 9200}


def test_start_metrics_server_delegates_to_prometheus(monkeypatch):
    called = {"count": 0}

    def fake_start_http_server(port, *, addr):
        called["count"] += 1
        called["host"] = addr
        called["port"] = port

    monkeypatch.setattr(server.prometheus_client, "start_http_server", fake_start_http_server)

    server.start_metrics_server(host="0.0.0.0", port=9100)

    assert called == {"count": 1, "host": "0.0.0.0", "port": 9100}


def test_start_metrics_server_does_not_trigger_health_or_collection(monkeypatch):
    called = {"health": False, "collector": False}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("health or collector logic should not run from the server module")

    monkeypatch.setattr(
        server.prometheus_client,
        "start_http_server",
        lambda port, *, addr: None,
    )
    monkeypatch.setattr(server, "check_database_health", fail_if_called, raising=False)
    monkeypatch.setattr(server, "collect_database_health", fail_if_called, raising=False)

    server.start_metrics_server()

    assert called == {"health": False, "collector": False}
