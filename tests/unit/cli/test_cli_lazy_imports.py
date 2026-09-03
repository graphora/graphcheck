import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from graphcheck import __version__

_COMMAND_MODULES = {
    "graphcheck.baselines",
    "graphcheck.connection_profiles",
    "graphcheck.contracts.profile",
    "graphcheck.contracts.results",
    "graphcheck.debug_diagnostics",
    "graphcheck.diff",
    "graphcheck.engine",
    "graphcheck.generation.client",
    "graphcheck.generation.service",
    "graphcheck.mcp.server",
    "graphcheck.neo4j_adapter",
    "graphcheck.observability.runner",
    "graphcheck.observability.server",
    "graphcheck.profiler",
    "graphcheck.project",
    "graphcheck.reporting",
}
_OPTIONAL_STACK_MODULES = {"anthropic", "google.genai", "instructor", "mcp", "openai"}
_COMMAND_MODULES |= _OPTIONAL_STACK_MODULES
_TELEMETRY_MODEL_MODULES = {
    "graphcheck.telemetry.collector",
    "graphcheck.telemetry.events",
    "graphcheck.telemetry.policy",
    "graphcheck.telemetry.posthog",
    "graphcheck.telemetry.runtime",
}
_VERSION_FORBIDDEN_MODULES = (
    _TELEMETRY_MODEL_MODULES
    | {
        "graphcheck.cli",
        "neo4j",
        "pydantic",
        "typer",
    }
    | _OPTIONAL_STACK_MODULES
)


@pytest.mark.parametrize("arguments", [["--help"], ["--version"]])
def test_fast_cli_paths_do_not_import_command_modules(arguments):
    source = Path(__file__).parents[3] / "src"
    environment = _isolated_environment()
    script = (
        "import json,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "from typer.testing import CliRunner;"
        "from graphcheck.cli import app;"
        f"result=CliRunner().invoke(app,{arguments!r});"
        "assert result.exit_code==0,result.exception;"
        f"print(json.dumps(sorted({_COMMAND_MODULES!r}&sys.modules.keys())))"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_console_version_fast_path_is_standard_library_only():
    completed = _run_bootstrap(["--version"], telemetry="0")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[0] == f"graphcheck {__version__}"
    assert _modules(completed) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["init", "--help"],
        ["debug", "--help"],
        ["profile", "--help"],
        ["generate", "--help"],
        ["report", "--help"],
        ["diff", "--help"],
        ["run", "--help"],
        ["baseline", "--help"],
    ],
)
def test_disabled_console_help_does_not_import_telemetry_models(arguments):
    completed = _run_bootstrap(arguments, telemetry="0")

    assert completed.returncode == 0, completed.stderr
    assert _modules(completed) == []


@pytest.mark.parametrize("persisted", [False, True], ids=["process", "persisted"])
def test_enabled_consent_loads_full_telemetry_only_after_bootstrap(tmp_path, persisted):
    config = tmp_path / "telemetry.json"
    if persisted:
        config.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "consent_version": "1.0",
                    "distinct_id": "55a068c3-fcd1-4f98-ae25-b66a3843b9d1",
                }
            ),
            encoding="utf-8",
        )
    completed = _run_bootstrap(
        ["--help"],
        telemetry=None if persisted else "1",
        config=config,
        patch_delivery=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "graphcheck.telemetry.runtime" in _modules(completed)


def test_bootstrap_probe_ignores_parent_pythonpath_sitecustomize(tmp_path, monkeypatch):
    contaminator = tmp_path / "contaminator"
    contaminator.mkdir()
    (contaminator / "sitecustomize.py").write_text(
        "import graphcheck.telemetry.runtime\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(contaminator))

    completed = _run_bootstrap(["--help"], telemetry="0")

    assert completed.returncode == 0, completed.stderr
    assert _modules(completed) == []


def _run_bootstrap(
    arguments,
    *,
    telemetry,
    config=None,
    patch_delivery=False,
):
    source = Path(__file__).parents[3] / "src"
    environment = _isolated_environment()
    if telemetry is None:
        environment.pop("GRAPHCHECK_TELEMETRY", None)
    else:
        environment["GRAPHCHECK_TELEMETRY"] = telemetry
    environment["GRAPHCHECK_TELEMETRY_CONFIG"] = str(config or source / "missing-consent.json")
    environment.pop("DO_NOT_TRACK", None)
    modules = (
        _VERSION_FORBIDDEN_MODULES
        if arguments == ["--version"]
        else _TELEMETRY_MODEL_MODULES | _OPTIONAL_STACK_MODULES
    )
    delivery_patch = (
        "import graphcheck.telemetry.release as release;release.POSTHOG_PROJECT_API_KEY=None;"
        if patch_delivery
        else ""
    )
    script = (
        "import json,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        f"sys.argv={['graphcheck', *arguments]!r};"
        f"{delivery_patch}"
        "from graphcheck.bootstrap import cli;"
        "exit_code=0;"
        "\ntry: cli()\n"
        "except SystemExit as exc: exit_code=int(exc.code or 0)\n"
        f"print(json.dumps({{'exit_code':exit_code,'modules':sorted({modules!r}&sys.modules.keys())}}))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def _modules(completed):
    return json.loads(completed.stdout.splitlines()[-1])["modules"]


def _isolated_environment():
    environment = os.environ.copy()
    for name in tuple(environment):
        if name == "PYTHONPATH" or name.startswith(("COVERAGE_", "COV_CORE_", "PYTEST_")):
            environment.pop(name)
    return environment
