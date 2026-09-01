from __future__ import annotations

import os

import pytest

from graphcheck.mcp.server import CheckListResponse
from graphcheck.reporting.writer import load_results


def _assert_spec01_contract(payload: dict) -> None:
    """The payload must be a complete, contract-valid SPEC-01 results.json."""
    assert payload["schema_version"] == "2.0"
    assert payload["run"]["id"]
    assert "score" in payload  # present, may be null
    assert isinstance(payload["suites"], list) and payload["suites"]
    assert isinstance(payload["checks"], list) and payload["checks"]
    totals = payload["totals"]
    assert totals["checks"] == len(payload["checks"])
    assert {"pass", "fail", "warn", "errored", "skipped"} <= totals.keys()
    for check in payload["checks"]:
        assert check["id"] and check["suite_id"] and check["verdict"] and check["severity"]
    # The whole payload re-validates against the frozen SPEC-01 model.
    results = load_results(payload)
    assert results.totals.checks == len(results.checks)


pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to run Neo4j container tests",
)


@pytest.mark.anyio
async def test_mcp_full_workflow_runs_a_suite_against_neo4j(mcp_project, mcp_client, neo4j_profile):
    # Point the project at the live container so run_suite can execute the engine end to end.
    (mcp_project / "profiles.yml").write_text(
        "default: local\n"
        "profiles:\n"
        "  local:\n"
        f"    uri: {neo4j_profile.uri}\n"
        f"    user: {neo4j_profile.user}\n"
        f"    password: {neo4j_profile.password}\n"
        f"    database: {neo4j_profile.database}\n",
        encoding="utf-8",
    )

    async with mcp_client(mcp_project) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {"list_checks", "run_suite", "get_results"}

        # 1. Discover a runnable suite.
        listed = await client.call_tool("list_checks", {})
        assert listed.is_error is False
        response = CheckListResponse.model_validate(listed.structured_content)
        suite_id = next(suite.suite for suite in response.suites if suite.suite == "smoke")

        # 2. Run it against the live database and prove the response is a full SPEC-01 result.
        run = await client.call_tool("run_suite", {"suite": suite_id})
        assert run.is_error is False
        assert run.structured_content is not None
        _assert_spec01_contract(run.structured_content)
        run_results = load_results(run.structured_content)
        run_id = run_results.run.id
        # The discovered suite actually ran and produced a verdict.
        assert any(check.suite_id == suite_id for check in run_results.checks)

        # 3. The most recent result is the run we just executed, and is equally contract-valid.
        latest = await client.call_tool("get_results", {})
        assert latest.is_error is False
        assert latest.structured_content is not None
        _assert_spec01_contract(latest.structured_content)
        latest_results = load_results(latest.structured_content)
        assert latest_results.run.id == run_id
        assert latest_results.totals == run_results.totals
        assert [check.id for check in latest_results.checks] == [
            check.id for check in run_results.checks
        ]
