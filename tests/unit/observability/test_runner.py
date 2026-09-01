from graphcheck.observability import runner


def test_run_monitor_starts_server_and_collects_on_interval_until_interrupted(monkeypatch):
    client = object()
    events = []

    def fake_collect(received):
        events.append(("collect", received))
        if sum(event[0] == "collect" for event in events) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        runner,
        "start_metrics_server",
        lambda host, port: events.append(("server", host, port)),
    )
    monkeypatch.setattr(runner, "collect_database_health", fake_collect)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    runner.run_monitor(client, interval_seconds=5, host="127.0.0.1", port=9200)

    assert events == [
        ("server", "127.0.0.1", 9200),
        ("collect", client),
        ("sleep", 5),
        ("collect", client),
        ("sleep", 5),
        ("collect", client),
    ]
