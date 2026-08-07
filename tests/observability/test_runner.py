from graphcheck.observability import runner


def test_run_monitor_starts_server_once(monkeypatch):
    calls = {"server": 0}

    def fake_start_metrics_server(host, port):
        calls["server"] += 1
        calls["host"] = host
        calls["port"] = port

    def fake_collect(client):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "start_metrics_server", fake_start_metrics_server)
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)

    runner.run_monitor(client=object(), interval_seconds=15)

    assert calls["server"] == 1
    assert calls["host"] == runner.DEFAULT_HOST
    assert calls["port"] == runner.DEFAULT_PORT


def test_run_monitor_calls_collector_repeatedly(monkeypatch):
    calls = {"count": 0}

    def fake_start_metrics_server(host, port):
        pass

    def fake_collect(client):
        calls["count"] += 1
        if calls["count"] >= 3:
            raise KeyboardInterrupt

    def fake_sleep(seconds):
        assert seconds == 5

    monkeypatch.setattr(runner, "start_metrics_server", fake_start_metrics_server)
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)
    monkeypatch.setattr(runner.time, "sleep", fake_sleep)

    runner.run_monitor(client=object(), interval_seconds=5)

    assert calls["count"] == 3


def test_run_monitor_respects_interval_seconds(monkeypatch):
    seen = []
    calls = {"collect": 0}

    def fake_start_metrics_server(host, port):
        pass

    def fake_collect(client):
        calls["collect"] += 1
        if calls["collect"] > 1:
            raise KeyboardInterrupt

    def fake_sleep(seconds):
        seen.append(seconds)

    monkeypatch.setattr(runner, "start_metrics_server", fake_start_metrics_server)
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)
    monkeypatch.setattr(runner.time, "sleep", fake_sleep)

    runner.run_monitor(client=object(), interval_seconds=7)

    assert seen == [7]


def test_run_monitor_exits_cleanly_on_keyboard_interrupt(monkeypatch):
    calls = {"collect": 0}

    def fake_start_metrics_server(host, port):
        pass

    def fake_collect(client):
        calls["collect"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "start_metrics_server", fake_start_metrics_server)
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    runner.run_monitor(client=object())

    assert calls["collect"] == 1


def test_run_monitor_delegates_to_server_and_collector(monkeypatch):
    calls = {"server": 0, "collect": 0}

    def fake_start_metrics_server(host, port):
        calls["server"] += 1
        assert host == "127.0.0.1"
        assert port == 9200

    def fake_collect(client):
        calls["collect"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "start_metrics_server", fake_start_metrics_server)
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    runner.run_monitor(client=object(), interval_seconds=1, host="127.0.0.1", port=9200)

    assert calls == {"server": 1, "collect": 1}
