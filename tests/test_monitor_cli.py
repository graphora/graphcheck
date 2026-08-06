from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.observability.server import DEFAULT_PORT

runner = CliRunner()


class RecordingClient:
    def __init__(self, profile):
        self.profile = profile
        self.closed = False

    def close(self):
        self.closed = True


def _configure_monitor(monkeypatch, tmp_path):
    selected_profile = object()
    created = []
    calls = []

    def client_factory(profile):
        client = RecordingClient(profile)
        created.append(client)
        return client

    def fake_run_monitor(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("graphcheck.cli.find_project_root", lambda: tmp_path)
    monkeypatch.setattr("graphcheck.cli.load_profiles", lambda root: "profiles")
    monkeypatch.setattr(
        "graphcheck.cli.select_profile",
        lambda profiles, name: (name or "local", selected_profile),
    )
    monkeypatch.setattr("graphcheck.cli.Neo4jClient", client_factory)
    monkeypatch.setattr("graphcheck.cli.run_monitor", fake_run_monitor)
    return selected_profile, created, calls


def test_monitor_constructs_existing_client_and_delegates(monkeypatch, tmp_path):
    selected_profile, created, calls = _configure_monitor(monkeypatch, tmp_path)

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 0
    assert len(created) == 1
    assert created[0].profile is selected_profile
    assert calls == [
        {
            "client": created[0],
            "interval_seconds": 15,
            "host": "127.0.0.1",
            "port": DEFAULT_PORT,
        }
    ]
    assert created[0].closed is True
    assert f"Metrics endpoint: http://127.0.0.1:{DEFAULT_PORT}/metrics" in result.stdout


def test_monitor_forwards_host_port_interval_and_profile(monkeypatch, tmp_path):
    _, created, calls = _configure_monitor(monkeypatch, tmp_path)
    selected_names = []
    monkeypatch.setattr(
        "graphcheck.cli.select_profile",
        lambda profiles, name: (selected_names.append(name) or name, object()),
    )

    result = runner.invoke(
        app,
        [
            "monitor",
            "--profile",
            "staging",
            "--host",
            "0.0.0.0",
            "--port",
            "9200",
            "--interval",
            "7",
        ],
    )

    assert result.exit_code == 0
    assert selected_names == ["staging"]
    assert calls == [
        {
            "client": created[0],
            "interval_seconds": 7,
            "host": "0.0.0.0",
            "port": 9200,
        }
    ]
    assert "Metrics endpoint: http://localhost:9200/metrics" in result.stdout
    assert "Health check interval: 7 seconds" in result.stdout


def test_monitor_reports_metrics_server_startup_failure(monkeypatch, tmp_path):
    _, created, _ = _configure_monitor(monkeypatch, tmp_path)

    def fail_startup(**kwargs):
        raise OSError("address already in use")

    monkeypatch.setattr("graphcheck.cli.run_monitor", fail_startup)

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 1
    assert "Unable to start GraphCheck monitoring." in result.stderr
    assert f"Failed to start metrics server on 127.0.0.1:{DEFAULT_PORT}." in result.stderr
    assert "The port may already be in use." in result.stderr
    assert "Traceback" not in result.stderr
    assert created[0].closed is True


def test_monitor_cli_contains_no_monitoring_logic(monkeypatch, tmp_path):
    _, created, calls = _configure_monitor(monkeypatch, tmp_path)

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 0
    assert calls[0]["client"] is created[0]
    assert not hasattr(created[0], "run_read")
