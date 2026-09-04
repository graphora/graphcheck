from __future__ import annotations

from pathlib import Path

import pytest

from graphcheck.application.run import RunOutcome
from graphcheck.errors import GraphCheckError
from graphcheck.mcp import adapter
from graphcheck.reporting.writer import load_results

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"


def test_run_suite_enables_read_only_verification_and_surfaces_artifact_failure(monkeypatch):
    results = load_results(FIXTURES / "results.complete.json")
    captured: dict[str, object] = {}

    def fake_execute_run(request):
        captured["verify"] = request.verify_read_only_credential
        return RunOutcome(
            results=results,
            results_path=None,
            report_path=None,
            artifact_error=OSError("disk full"),
        )

    monkeypatch.setattr(adapter, "execute_run", fake_execute_run)

    with pytest.raises(GraphCheckError) as raised:
        adapter.run_suite("smoke")

    # run_suite enforces the same read-only credential guard as `graphcheck run`...
    assert captured["verify"] is True
    # ...and a run whose artifacts could not be published is an explicit MCP tool error,
    # never an apparently successful run that get_results cannot find.
    assert raised.value.error.code == "mcp.artifact_write_failed"
