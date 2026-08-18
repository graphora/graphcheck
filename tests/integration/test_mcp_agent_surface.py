from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from graphcheck.mcp.server import CheckListResponse


@pytest.mark.anyio
async def test_mcp_agent_surface_uses_modern_protocol_and_supports_discovery(
    mcp_project, mcp_client
):
    async with mcp_client(mcp_project) as client:
        # The 2026-07-28 spec-GA protocol is negotiated via discovery, without the legacy
        # initialize handshake.
        assert client.protocol_version == "2026-07-28"

        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        # Exactly the three documented tools are exposed — no more, no fewer.
        assert names == {"list_checks", "run_suite", "get_results"}

        # Discovery: list_checks returns the configured suites so an agent can choose a
        # valid run_suite argument, and it exposes each suite's checks without executing them.
        listed = await client.call_tool("list_checks", {})
        assert listed.is_error is False
        assert listed.structured_content is not None
        response = CheckListResponse.model_validate(listed.structured_content)
        suite_ids = {suite.suite for suite in response.suites}
        assert "smoke" in suite_ids
        smoke = next(suite for suite in response.suites if suite.suite == "smoke")
        assert smoke.checks
        assert all(check.id for check in smoke.checks)

        # get_results defaults to the most recent run and returns the SPEC-01 results.json.
        latest = await client.call_tool("get_results", {})
        assert latest.is_error is False
        assert latest.structured_content is not None
        assert latest.structured_content["schema_version"]
        assert "run" in latest.structured_content


@pytest.mark.anyio
async def test_mcp_server_still_supports_legacy_initialize_clients(mcp_project):
    # Backward compatibility: a legacy client that performs the initialize handshake can
    # still discover the tools.
    params = StdioServerParameters(
        command="graphcheck", args=["mcp", "serve"], cwd=str(mcp_project)
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {"list_checks", "run_suite", "get_results"}
