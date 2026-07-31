import json
import sys

import pytest
from typer.testing import CliRunner

from graphcheck.cli import app, cli
from graphcheck.telemetry import posthog as posthog_module
from graphcheck.telemetry import runtime as runtime_module
from graphcheck.telemetry.policy import CommandName

runner = CliRunner()

_DEFAULT_OFF_COMMANDS = (
    (CommandName.INIT, ("init", "--help")),
    (CommandName.DEBUG, ("debug", "--help")),
    (CommandName.RUN, ("run", "--help")),
    (CommandName.REPORT, ("report", "--help")),
    # Help invocations exercise the parse-time boundary without running command side effects.
    (CommandName.PROFILE, ("profile", "--help")),
    (CommandName.GENERATE, ("generate", "--help")),
    (CommandName.DIFF, ("diff", "--help")),
    (CommandName.BASELINE, ("baseline", "--help")),
    (CommandName.TELEMETRY, ("telemetry", "status")),
    (CommandName.TELEMETRY, ("telemetry", "preview")),
    (CommandName.TELEMETRY, ("telemetry", "disable")),
    (CommandName.TELEMETRY, ("telemetry", "reset-id")),
    (CommandName.OTHER, ("--version",)),
)


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


def test_default_off_command_matrix_covers_every_allowlisted_command():
    assert {command for command, _ in _DEFAULT_OFF_COMMANDS} == set(CommandName)


@pytest.mark.parametrize(
    ("command", "arguments"),
    _DEFAULT_OFF_COMMANDS,
    ids=[
        "init",
        "debug",
        "run",
        "report",
        "profile-future",
        "generate",
        "diff-future",
        "baseline-future",
        "telemetry-status",
        "telemetry-preview",
        "telemetry-disable",
        "telemetry-reset-id",
        "other",
    ],
)
def test_fresh_install_never_constructs_or_sends_telemetry_before_opt_in(
    tmp_path,
    monkeypatch,
    capsys,
    command,
    arguments,
):
    config = tmp_path / f"{command.value}-telemetry.json"
    forbidden_calls = []

    def forbidden(component):
        def fail(*args, **kwargs):
            forbidden_calls.append((component, args, kwargs))
            raise AssertionError(f"default-off telemetry invoked {component}")

        return fail

    monkeypatch.setenv("GRAPHCHECK_TELEMETRY_CONFIG", str(config))
    monkeypatch.setenv("GRAPHCHECK_POSTHOG_API_KEY", "phc_should_never_be_used")
    monkeypatch.delenv("GRAPHCHECK_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(runtime_module, "TelemetryCollector", forbidden("collector"))
    monkeypatch.setattr(runtime_module, "create_posthog_adapter", forbidden("adapter"))
    monkeypatch.setattr(
        posthog_module.HttpPostHogTransport,
        "send",
        forbidden("transport"),
    )
    monkeypatch.setattr(
        posthog_module.urllib.request,
        "urlopen",
        forbidden("network"),
    )
    monkeypatch.setattr(sys, "argv", ["graphcheck", *arguments])

    with pytest.raises(SystemExit):
        cli()
    capsys.readouterr()

    assert forbidden_calls == []
    if config.exists():
        stored = json.loads(config.read_text(encoding="utf-8"))
        assert stored.get("enabled") is False
        assert stored.get("distinct_id") is None
