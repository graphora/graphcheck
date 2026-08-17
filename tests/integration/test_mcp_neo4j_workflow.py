from __future__ import annotations

import os

import pytest

from graphcheck.mcp.server import CheckListResponse

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

        # 2. Run it against the live database.
        run = await client.call_tool("run_suite", {"suite": suite_id})
        assert run.is_error is False
        assert run.structured_content is not None
        assert run.structured_content["schema_version"]
        run_id = run.structured_content["run"]["id"]

        # 3. The most recent result is retrievable and is the run we just executed.
        latest = await client.call_tool("get_results", {})
        assert latest.is_error is False
        assert latest.structured_content is not None
        assert latest.structured_content["run"]["id"] == run_id
