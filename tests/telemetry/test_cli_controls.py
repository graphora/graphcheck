import json

from typer.testing import CliRunner

from graphcheck.cli import app

runner = CliRunner()


def test_preview_never_persists_or_sends(tmp_path):
    config = tmp_path / "telemetry.json"
    result = runner.invoke(
        app,
        ["telemetry", "preview"],
        env={"GRAPHCHECK_TELEMETRY_CONFIG": str(config)},
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sent"] is False
    assert {event["event"] for event in payload["events"]} == {
        "graphcheck_run_started",
        "graphcheck_check_processed",
        "graphcheck_run_completed",
        "graphcheck_engine_faulted",
        "graphcheck_command_completed",
        "graphcheck_profile_completed",
    }
    assert not config.exists()


def test_enable_status_reset_and_disable_are_user_level_controls(tmp_path):
    config = tmp_path / "telemetry.json"
    env = {"GRAPHCHECK_TELEMETRY_CONFIG": str(config)}

    enabled = runner.invoke(app, ["telemetry", "enable"], env=env)
    assert enabled.exit_code == 0
    assert "delivery is not configured" in enabled.stdout.lower()
    status = runner.invoke(app, ["telemetry", "status"], env=env)
    assert "Telemetry: enabled" in status.stdout
    assert "Delivery: not configured" in status.stdout
    first = json.loads(config.read_text(encoding="utf-8"))["distinct_id"]

    assert runner.invoke(app, ["telemetry", "reset-id"], env=env).exit_code == 0
    second = json.loads(config.read_text(encoding="utf-8"))["distinct_id"]
    assert second != first

    assert runner.invoke(app, ["telemetry", "disable"], env=env).exit_code == 0
    status = runner.invoke(app, ["telemetry", "status"], env=env)
    assert "Telemetry: disabled" in status.stdout
