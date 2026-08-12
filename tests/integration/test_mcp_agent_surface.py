from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from graphcheck.mcp.server import CheckListResponse


@pytest.mark.anyio
async def test_mcp_agent_surface():
    server = StdioServerParameters(
        command="graphcheck",
        args=["mcp", "serve"],
    )

    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()

        names = {tool.name for tool in tools.tools}

        assert "list_checks" in names
        assert "run_suite" in names
        assert "get_results" in names

        list_checks_tool = next(tool for tool in tools.tools if tool.name == "list_checks")

        assert list_checks_tool.output_schema is not None

        result = await session.call_tool(
            "list_checks",
            {},
        )

        assert result is not None
        assert result.structured_content is not None

        response = CheckListResponse.model_validate(result.structured_content)

        assert response.checks
