import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_COMMAND_MODULES = {
    "graphcheck.baselines",
    "graphcheck.connection_profiles",
    "graphcheck.contracts.profile",
    "graphcheck.contracts.results",
    "graphcheck.debug_diagnostics",
    "graphcheck.diff",
    "graphcheck.engine",
    "graphcheck.neo4j_adapter",
    "graphcheck.profiler",
    "graphcheck.project",
    "graphcheck.reporting",
}


@pytest.mark.parametrize("arguments", [["--help"], ["--version"]])
def test_fast_cli_paths_do_not_import_command_modules(arguments):
    source = Path(__file__).parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(source), environment.get("PYTHONPATH")) if path
    )
    script = (
        "import json,sys;"
        "from typer.testing import CliRunner;"
        "from graphcheck.cli import app;"
        f"result=CliRunner().invoke(app,{arguments!r});"
        "assert result.exit_code==0,result.exception;"
        f"print(json.dumps(sorted({_COMMAND_MODULES!r}&sys.modules.keys())))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
